"""Rule-based visibility checks (geometry + text attributes). Thresholds come from VisibilitySettings."""

from __future__ import annotations

import re
from collections import defaultdict

from rapidfuzz import fuzz

from app.core.config import VisibilitySettings
from app.domain.document import BBox, ParsedDocument, TextSpan
from app.domain.enums import SUSPICIOUS_TINY_FONT, HiddenReason, SpanSource
from app.services.visibility.contrast import contrast_ratio

_NONSPACE = re.compile(r"\S")


def nonspace_len(text: str) -> int:
    return len(_NONSPACE.findall(text))


def area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def intersection(a: BBox, b: BBox) -> BBox | None:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def inter_area(a: BBox, b: BBox) -> float:
    i = intersection(a, b)
    return area(i) if i else 0.0


# --------------------------------------------------------------------------------------------- PDF


def detect_ocr_layer_pages(doc: ParsedDocument, vis: VisibilitySettings) -> set[int]:
    """Pages that are a scanned image with an invisible OCR text layer on top (a normal, legit pattern)."""
    pages: set[int] = set()
    for pno, (w, h) in enumerate(doc.page_sizes, start=1):
        page_area = w * h
        img_area = sum(inter_area(im.bbox, (0, 0, w, h)) for im in doc.images if im.page == pno)
        invisible = [s for s in doc.spans if s.page == pno and s.render_mode == 3 and not s.is_blank]
        if invisible and page_area and img_area / page_area >= vis.ocr_layer_image_coverage:
            pages.add(pno)
    return pages


def apply_pdf_rules(doc: ParsedDocument, vis: VisibilitySettings) -> None:
    covers_by_page: dict[int, list[tuple[int, BBox]]] = defaultdict(list)
    for shape in doc.shapes:
        if shape.fill_opacity >= vis.cover_min_opacity:
            covers_by_page[shape.page].append((shape.seqno, shape.bbox))
    for img in doc.images:
        if not img.has_mask:
            covers_by_page[img.page].append((img.seqno, img.bbox))

    for span in doc.spans:
        if span.is_blank:
            continue
        if span.source == SpanSource.HIDDEN_LAYER:
            span.hide(HiddenReason.HIDDEN_LAYER)
        if span.source == SpanSource.ANNOTATION:
            if span.attrs.get("annot_type") != "FreeText":
                span.hide(HiddenReason.ANNOTATION_ONLY)
            continue
        if span.render_mode in (3, 7) and span.page not in doc.ocr_layer_pages:
            span.hide(HiddenReason.INVISIBLE_RENDER_MODE, span.render_mode)
        if span.opacity < vis.low_opacity:
            span.hide(HiddenReason.LOW_OPACITY, span.opacity)
        _tiny_font(span, vis)
        if span.bbox is None:
            continue
        w, h = doc.page_sizes[span.page - 1]
        box_area = area(span.bbox)
        if box_area > 0:
            outside = 1 - inter_area(span.bbox, (0.0, 0.0, w, h)) / box_area
            if outside >= vis.off_page_min_outside:
                span.hide(HiddenReason.OFF_PAGE, outside)
                continue
            covered = _coverage(span, covers_by_page.get(span.page, []))
            if covered >= vis.covered_min_fraction:
                span.hide(HiddenReason.COVERED, covered)
    _stacked_text(doc, vis)


def _tiny_font(span: TextSpan, vis: VisibilitySettings) -> None:
    size = span.effective_size
    if size <= 0:
        return
    if size < vis.tiny_font_hidden_pt:
        span.hide(HiddenReason.TINY_FONT, size)
    elif size < vis.tiny_font_suspicious_pt and nonspace_len(span.text) >= vis.tiny_font_suspicious_min_chars:
        span.suspicious_reasons.append(SUSPICIOUS_TINY_FONT)
        span.measurements[SUSPICIOUS_TINY_FONT] = round(size, 3)


def _coverage(span: TextSpan, covers: list[tuple[int, BBox]]) -> float:
    """Share of the span bbox covered by opaque shapes/images drawn *after* it."""
    assert span.bbox is not None
    later = [b for seq, b in covers if seq > span.seqno >= 0]
    if not later or area(span.bbox) <= 0:
        return 0.0
    x0, y0, x1, y1 = span.bbox
    steps = 24  # sample the union of covers on a coarse grid inside the span bbox
    hits = 0
    for i in range(steps):
        px = x0 + (i + 0.5) * (x1 - x0) / steps
        for j in range(steps):
            py = y0 + (j + 0.5) * (y1 - y0) / steps
            if any(b[0] <= px <= b[2] and b[1] <= py <= b[3] for b in later):
                hits += 1
    return hits / (steps * steps)


def _stacked_text(doc: ParsedDocument, vis: VisibilitySettings) -> None:
    """Different text drawn at the same position: the earlier (underneath) span is unreadable."""
    by_page: dict[int, list[TextSpan]] = defaultdict(list)
    for s in doc.spans:
        if (
            s.bbox is not None
            and not s.is_blank
            and s.source != SpanSource.ANNOTATION
            and nonspace_len(s.text) >= 3
        ):
            by_page[s.page].append(s)
    for spans in by_page.values():
        spans.sort(key=lambda s: s.bbox[1])  # type: ignore[index]
        for i, a in enumerate(spans):
            for b in spans[i + 1 :]:
                if b.bbox[1] > a.bbox[3]:  # type: ignore[index]
                    break
                if a.line_id == b.line_id and a.seqno == b.seqno:
                    continue
                overlap = inter_area(a.bbox, b.bbox)  # type: ignore[arg-type]
                smaller = min(area(a.bbox), area(b.bbox))  # type: ignore[arg-type]
                if smaller <= 0 or overlap / smaller < vis.stacked_min_overlap:
                    continue
                if fuzz.ratio(a.text.strip().lower(), b.text.strip().lower()) >= vis.stacked_same_text_ratio:
                    continue  # same text drawn twice: faux bold / shadow effect
                under = a if a.seqno < b.seqno else b
                under.hide(HiddenReason.STACKED_TEXT, overlap / smaller)


# --------------------------------------------------------------------------------------------- DOCX


def apply_docx_rules(doc: ParsedDocument, vis: VisibilitySettings) -> None:
    for span in doc.spans:
        if span.is_blank:
            continue
        kind: str | None = None
        measured: float | None = None
        if span.source in (SpanSource.COMMENT, SpanSource.ALT_TEXT):
            kind = str(span.source)
        elif span.attrs.get("vanish"):
            kind = "vanish"
        elif span.attrs.get("deleted"):
            kind = "tracked_deletion"
        elif span.effective_size and span.effective_size < vis.tiny_font_hidden_pt:
            kind, measured = "tiny_font", span.effective_size
        elif not span.attrs.get("auto_color") and span.background_rgb is not None:
            ratio = contrast_ratio(span.color_rgb, span.background_rgb)
            if ratio < vis.low_contrast_ratio:
                kind, measured = "same_color", ratio
        if kind:
            span.attrs["docx_hidden"] = kind
            span.hide(HiddenReason.DOCX_HIDDEN, measured)
        elif (
            span.effective_size
            and span.effective_size < vis.tiny_font_suspicious_pt
            and nonspace_len(span.text) >= vis.tiny_font_suspicious_min_chars
        ):
            span.suspicious_reasons.append(SUSPICIOUS_TINY_FONT)
            span.measurements[SUSPICIOUS_TINY_FONT] = span.effective_size
