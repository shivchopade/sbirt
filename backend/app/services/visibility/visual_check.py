"""Pixel-level checks on the page render (rendered once per page at VisibilitySettings.render_dpi).

* LOW_CONTRAST compares the text colour (blended with its opacity) against the *actual* background under the
  span — the most frequent rendered colour in its bbox — so white text on a dark sidebar or on a photo header is
  fine while white-on-white is not. Never "is the text white?".
* NOT_RENDERED_VISIBLY is an independent check: if the crop of a span is (near-)uniform, nothing was drawn
  there that a human could read, whatever trick was used (clipping, blend modes, covering, ...).
"""

from __future__ import annotations

import logging

from app.core.config import Settings, VisibilitySettings
from app.domain.document import ParsedDocument, TextSpan
from app.domain.enums import NON_PAGE_SOURCES, HiddenReason, SpanSource
from app.services.visibility.contrast import (
    blend,
    contrast_ratio,
    crop,
    distance,
    estimate_background,
    pixel_stats,
)
from app.services.visibility.rules import area, inter_area, nonspace_len

log = logging.getLogger(__name__)

_PIXEL_OVERRIDABLE = {HiddenReason.COVERED}


def apply_pixel_checks(doc: ParsedDocument, vis: VisibilitySettings) -> None:
    for span in doc.spans:
        if (
            span.is_blank
            or span.bbox is None
            or span.source in NON_PAGE_SOURCES
            or span.source == SpanSource.HIDDEN_LAYER
            or HiddenReason.OFF_PAGE in span.hidden_reasons
        ):
            continue
        render = doc.renders.get(span.page)
        region = crop(render, span.bbox, doc.render_dpi) if render is not None else None
        if region is None or region.size == 0:
            _rule_only_contrast(doc, span, vis)
            continue
        stats = pixel_stats(region, span.color_rgb, vis.pixel_ink_delta)
        background = estimate_background(stats, span.color_rgb, vis.pixel_ink_delta, vis.bg_ink_share)
        effective = blend(span.color_rgb, background, span.opacity)
        ratio = contrast_ratio(effective, background)
        span.background_rgb = background
        span.measurements["contrast"] = round(ratio, 3)
        span.measurements["ink_fraction"] = round(stats.ink_fraction, 4)
        if ratio < vis.low_contrast_ratio and span.render_mode != 3:
            span.hide(HiddenReason.LOW_CONTRAST, ratio)

        checkable = (
            nonspace_len(span.text) >= vis.pixel_check_min_chars
            and stats.height >= vis.pixel_check_min_height_px
        )
        if not checkable:
            continue
        if stats.ink_fraction < vis.pixel_min_ink_fraction:  # (near-)uniform crop
            if span.visible:
                span.hide(HiddenReason.NOT_RENDERED_VISIBLY, stats.ink_fraction)
                span.confidence = "medium"
            else:
                span.confidence = "high"  # rule + pixel agreement
            continue
        # ink is present: a "covered" verdict is contradicted when the ink looks like the text itself
        text_visible = (
            stats.text_like_fraction >= vis.pixel_min_ink_fraction
            and distance(effective, background) > vis.pixel_ink_delta
        )
        if text_visible:
            for reason in [r for r in span.hidden_reasons if r in _PIXEL_OVERRIDABLE]:
                span.hidden_reasons.remove(reason)
                span.measurements.pop(str(reason), None)
            if not span.hidden_reasons:
                span.visible = True
        if not span.visible:
            span.confidence = span.confidence or "medium"


def _rule_only_contrast(doc: ParsedDocument, span: TextSpan, vis: VisibilitySettings) -> None:
    """No render available: background = topmost opaque shape drawn before the span, else the page colour."""
    background = doc.page_background
    best_seq = -1
    for shape in doc.shapes:
        if (
            shape.page != span.page
            or shape.fill_rgb is None
            or shape.seqno >= span.seqno
            or shape.seqno < best_seq
        ):
            continue
        if span.bbox and area(span.bbox) > 0 and inter_area(shape.bbox, span.bbox) / area(span.bbox) >= 0.5:
            background, best_seq = shape.fill_rgb, shape.seqno
    effective = blend(span.color_rgb, background, span.opacity)
    ratio = contrast_ratio(effective, background)
    span.background_rgb = background
    span.measurements["contrast"] = round(ratio, 3)
    if ratio < vis.low_contrast_ratio and span.render_mode != 3:
        span.hide(HiddenReason.LOW_CONTRAST, ratio)


def apply_ocr(doc: ParsedDocument, settings: Settings) -> None:
    """Optional (OCR__ENABLED=true): words the parser sees but OCR cannot find near their bbox are hidden."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        doc.warnings.append("OCR enabled but pytesseract is not installed")
        return
    scale = doc.render_dpi / 72.0
    for pno, render in doc.renders.items():
        try:
            data = pytesseract.image_to_data(
                Image.fromarray(render), lang=settings.ocr.lang, output_type=pytesseract.Output.DICT
            )
        except Exception as exc:  # pragma: no cover - depends on the tesseract binary
            doc.warnings.append(f"OCR failed on page {pno}: {exc.__class__.__name__}")
            return
        words = []
        for i, raw in enumerate(data["text"]):
            if raw.strip() and float(data["conf"][i]) >= settings.ocr.min_word_conf:
                left, top = data["left"][i] / scale, data["top"][i] / scale
                box = (left, top, left + data["width"][i] / scale, top + data["height"][i] / scale)
                words.append((raw.strip().lower(), box))
        for span in doc.spans:
            if span.page != pno or not span.visible or span.bbox is None or nonspace_len(span.text) < 3:
                continue
            tokens = [t.lower() for t in span.text.split() if len(t) >= 3]
            near = [w for w, b in words if inter_area(b, span.bbox) > 0]
            if tokens and not any(any(t[:4] in w or w[:4] in t for w in near) for t in tokens):
                span.hide(HiddenReason.OCR_MISMATCH)
