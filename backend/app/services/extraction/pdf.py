"""PDF → ParsedDocument with per-span styling, position and draw order.

Two PyMuPDF views are joined on exact character origins:

* ``get_text("rawdict")`` gives reading order, lines and spaces. ``TEXT_MEDIABOX_CLIP`` must be cleared
  **and** ``clip=INFINITE_RECT()`` passed, otherwise text outside the page box silently disappears.
* ``get_texttrace()`` gives what rawdict lacks: render mode (type 3 = invisible), exact opacity,
  optional-content layer and the draw sequence number shared with ``get_drawings()`` / ``get_bboxlog()``.

Text inside optional-content layers that are OFF is invisible to both; it is recovered by re-opening the
document with every layer switched on and diffing the characters.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

import numpy as np
import pymupdf

from app.domain.document import BBox, DrawOp, ImageBox, ParsedDocument, ShapeBox, TextSpan
from app.domain.enums import SpanSource

log = logging.getLogger(__name__)

RAW_FLAGS = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_MEDIABOX_CLIP & ~pymupdf.TEXT_PRESERVE_IMAGES
_XMP_TEXT = re.compile(r">([^<>]{2,})<")


def extract_pdf(
    data: bytes, *, file_name: str, sha256: str, render_dpi: int = 150, render: bool = True
) -> ParsedDocument:
    doc = pymupdf.open(stream=data, filetype="pdf")
    parsed = ParsedDocument(
        file_name=file_name,
        file_type="pdf",
        sha256=sha256,
        size_bytes=len(data),
        page_count=doc.page_count,
        render_dpi=render_dpi,
    )
    with doc:
        parsed.metadata = _metadata(doc)
        line_counter = [0]
        for page in doc:
            pno = page.number + 1
            parsed.page_sizes.append((page.rect.width, page.rect.height))
            _extract_page(page, pno, parsed, line_counter)
            if render:
                parsed.renders[pno] = render_page(page, render_dpi)
        _extract_off_layers(data, doc, parsed, line_counter)
    parsed.body_font_size = _body_font_size(parsed.spans)
    return parsed


def render_page(page: pymupdf.Page, dpi: int) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()


# ------------------------------------------------------------------------------------------------ page


def _extract_page(page: pymupdf.Page, pno: int, parsed: ParsedDocument, line_counter: list[int]) -> None:
    trace = page.get_texttrace()
    exact: dict[tuple[str, int, int], list[int]] = {}
    by_pos: dict[tuple[int, int], list[int]] = {}
    for ti, ts in enumerate(trace):
        for uc, _gid, origin, _bbox in ts["chars"]:
            key = (_q(origin[0]), _q(origin[1]))
            exact.setdefault((chr(uc) if uc > 0 else "", *key), []).append(ti)
            by_pos.setdefault(key, []).append(ti)

    matched: set[int] = set()
    raw = page.get_text("rawdict", flags=RAW_FLAGS, clip=pymupdf.INFINITE_RECT())
    for block_no, block in enumerate(raw.get("blocks", [])):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            line_counter[0] += 1
            line_id = line_counter[0]
            for sp in line.get("spans", []):
                for run_chars, ti, touched in _split_by_trace(sp["chars"], exact, by_pos, trace):
                    matched |= touched
                    span = _span_from_raw(
                        sp, run_chars, line["dir"], trace[ti] if ti is not None else None, pno
                    )
                    span.line_id, span.block_id = line_id, block_no
                    parsed.spans.append(span)

    # Anything the trace saw that rawdict did not report (defensive: never lose drawn text).
    for ti, ts in enumerate(trace):
        if ti in matched or not ts["chars"]:
            continue
        text = "".join(chr(c[0]) for c in ts["chars"] if c[0] > 0)
        if not text.strip():
            continue
        line_counter[0] += 1
        span = _span_from_trace(ts, text, pno)
        span.line_id = line_counter[0]
        parsed.spans.append(span)

    _extract_graphics(page, pno, parsed)
    _extract_annotations(page, pno, parsed, line_counter)


def _q(v: float) -> int:
    return round(v * 10)


def _pick(candidates: list[int] | None, prev: int | None) -> int | None:
    """Several trace spans can draw a glyph at the same spot (stacked text): prefer continuity."""
    if not candidates:
        return None
    return prev if prev in candidates else candidates[0]


def _split_by_trace(
    chars: list[dict[str, Any]],
    exact: dict[tuple[str, int, int], list[int]],
    by_pos: dict[tuple[int, int], list[int]],
    trace: list[dict[str, Any]],
) -> list[tuple[list[dict[str, Any]], int | None, set[int]]]:
    """Split a rawdict span wherever the underlying trace attributes (render mode/opacity/layer) change.

    Returns (chars, representative trace index = latest drawn, every trace index touched).
    """
    runs: list[tuple[list[dict[str, Any]], int | None, set[int]]] = []
    prev_key: tuple | None = None
    prev_ti: int | None = None
    for ch in chars:
        key = (_q(ch["origin"][0]), _q(ch["origin"][1]))
        ti = _pick(exact.get((ch["c"], *key)), prev_ti)
        if ti is None and not ch.get("synthetic"):
            ti = _pick(by_pos.get(key), prev_ti)
        if ti is None:
            ti = prev_ti
        attr = _attr_key(trace[ti]) if ti is not None else None
        if runs and (attr == prev_key or ti is None):
            chars_, rep, touched = runs[-1]
            chars_.append(ch)
            if ti is not None:
                touched.add(ti)
                if rep is None or trace[ti]["seqno"] > trace[rep]["seqno"]:
                    runs[-1] = (chars_, ti, touched)
        else:
            runs.append(([ch], ti, {ti} if ti is not None else set()))
        prev_key, prev_ti = attr, ti
    return runs


def _attr_key(ts: dict[str, Any]) -> tuple:
    return (
        ts["type"],
        round(float(ts["opacity"]), 2),
        ts.get("layer") or "",
        tuple(round(c, 3) for c in ts["color"]),
    )


def _span_from_raw(
    sp: dict[str, Any], chars: list[dict[str, Any]], line_dir: tuple, ts: dict[str, Any] | None, pno: int
) -> TextSpan:
    text = "".join(ch["c"] for ch in chars)
    x0 = min(ch["bbox"][0] for ch in chars)
    y0 = min(ch["bbox"][1] for ch in chars)
    x1 = max(ch["bbox"][2] for ch in chars)
    y1 = max(ch["bbox"][3] for ch in chars)
    bbox: BBox = (x0, y0, x1, y1)
    size = float(sp.get("size", 0.0))
    color = _int_to_rgb(sp.get("color", 0))
    opacity = float(sp.get("alpha", 255)) / 255.0
    render_mode, seqno, layer, trace_type = 0, -1, None, None
    if ts is not None:
        trace_type = int(ts["type"])
        render_mode = {0: 0, 1: 1, 2: 7, 3: 3}.get(trace_type, 0)
        opacity = float(ts["opacity"])
        seqno = int(ts["seqno"])
        layer = ts.get("layer") or None
        if trace_type == 1:
            color = _trace_color(ts)
    flags = int(sp.get("flags", 0))
    font = sp.get("font", "")
    eff = effective_size(
        size, chars, line_dir, bbox, float(sp.get("ascender", 1.0)), float(sp.get("descender", -0.25))
    )
    return TextSpan(
        text=text,
        page=pno,
        bbox=bbox,
        font=font,
        declared_size=round(size, 3),
        effective_size=round(eff, 3),
        color_rgb=color,
        opacity=round(opacity, 4),
        render_mode=render_mode,
        seqno=seqno,
        layer=layer,
        bold=bool(flags & 16) or "bold" in font.lower(),
        italic=bool(flags & 2) or any(w in font.lower() for w in ("italic", "oblique")),
        attrs={"trace_type": trace_type} if trace_type is not None else {},
    )


def _span_from_trace(ts: dict[str, Any], text: str, pno: int) -> TextSpan:
    size = float(ts.get("size", 0.0))
    trace_type = int(ts["type"])
    return TextSpan(
        text=text,
        page=pno,
        bbox=tuple(float(v) for v in ts["bbox"]),  # type: ignore[arg-type]
        font=ts.get("font", ""),
        declared_size=round(size, 3),
        effective_size=round(size, 3),
        color_rgb=_trace_color(ts),
        opacity=round(float(ts["opacity"]), 4),
        render_mode={0: 0, 1: 1, 2: 7, 3: 3}.get(trace_type, 0),
        seqno=int(ts["seqno"]),
        layer=ts.get("layer") or None,
        attrs={"trace_type": trace_type, "trace_only": True},
    )


def effective_size(
    size: float, chars: list[dict[str, Any]], line_dir: tuple, bbox: BBox, asc: float, desc: float
) -> float:
    """Size as rendered: catches scaled, vertically squashed and horizontally squeezed text."""
    eff = size
    if len(chars) >= 3 and size > 0:
        (x0, y0), (x1, y1) = chars[0]["origin"], chars[-1]["origin"]
        advance = math.hypot(x1 - x0, y1 - y0) / (len(chars) - 1)
        ratio = advance / size
        if ratio < 0.15:  # normal text advances ~0.45–0.6 em per character
            eff = min(eff, size * ratio / 0.5)
    if abs(line_dir[0]) > 0.99 and bbox[3] > bbox[1]:
        em = asc - desc if (asc - desc) > 0.5 else 1.2
        eff = min(eff, (bbox[3] - bbox[1]) / em)
    elif abs(line_dir[1]) > 0.99 and bbox[2] > bbox[0]:
        em = asc - desc if (asc - desc) > 0.5 else 1.2
        eff = min(eff, (bbox[2] - bbox[0]) / em)
    return max(eff, 0.0)


def _int_to_rgb(value: int) -> tuple[int, int, int]:
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


def _trace_color(ts: dict[str, Any]) -> tuple[int, int, int]:
    comps = [float(c) for c in ts.get("color", (0.0,))]
    if len(comps) == 1:
        g = round(comps[0] * 255)
        return (g, g, g)
    if len(comps) == 4:  # CMYK → RGB
        c, m, y, k = comps
        return (
            round(255 * (1 - c) * (1 - k)),
            round(255 * (1 - m) * (1 - k)),
            round(255 * (1 - y) * (1 - k)),
        )
    r, g, b = comps[:3]
    return (round(r * 255), round(g * 255), round(b * 255))


# ------------------------------------------------------------------------------------------------ graphics


def _extract_graphics(page: pymupdf.Page, pno: int, parsed: ParsedDocument) -> None:
    try:
        log_entries = page.get_bboxlog()
    except Exception:  # pragma: no cover - defensive
        log_entries = []
    image_masks = {}
    try:
        for info in page.get_image_info(xrefs=False):
            image_masks[tuple(round(v, 1) for v in info["bbox"])] = bool(info.get("has-mask"))
    except Exception:  # pragma: no cover
        pass
    for seq, (kind, rect) in enumerate((e[0], e[1]) for e in log_entries):
        bbox = tuple(float(v) for v in rect)
        parsed.draw_log.append(DrawOp(page=pno, seqno=seq, kind=kind, bbox=bbox))  # type: ignore[arg-type]
        if kind == "fill-image":
            has_mask = image_masks.get(tuple(round(v, 1) for v in bbox), False)
            parsed.images.append(ImageBox(page=pno, bbox=bbox, seqno=seq, has_mask=has_mask))  # type: ignore[arg-type]
        elif kind == "fill-shade":
            parsed.shapes.append(ShapeBox(page=pno, bbox=bbox, seqno=seq, fill_rgb=None, fill_opacity=1.0))  # type: ignore[arg-type]
    try:
        drawings = page.get_drawings()
    except Exception:  # pragma: no cover
        drawings = []
    for d in drawings:
        fill = d.get("fill")
        if fill is None or d.get("type") not in ("f", "fs"):
            continue
        rect = d["rect"]
        parsed.shapes.append(
            ShapeBox(
                page=pno,
                bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                seqno=int(d.get("seqno", -1)),
                fill_rgb=tuple(round(c * 255) for c in fill[:3])
                if len(fill) >= 3
                else (round(fill[0] * 255),) * 3,  # type: ignore[arg-type]
                fill_opacity=float(d.get("fill_opacity") or 1.0),
            )
        )


def _extract_annotations(
    page: pymupdf.Page, pno: int, parsed: ParsedDocument, line_counter: list[int]
) -> None:
    for link in page.get_links():
        uri = link.get("uri")
        if uri:
            parsed.contact.links.append(uri)
    for annot in page.annots() or []:
        content = (annot.info or {}).get("content", "") or ""
        if not content.strip():
            continue
        line_counter[0] += 1
        r = annot.rect
        parsed.spans.append(
            TextSpan(
                text=content,
                page=pno,
                bbox=(r.x0, r.y0, r.x1, r.y1),
                source=SpanSource.ANNOTATION,
                line_id=line_counter[0],
                attrs={"annot_type": annot.type[1]},
            )
        )


# ------------------------------------------------------------------------------------------------ layers


def _extract_off_layers(
    data: bytes, doc: pymupdf.Document, parsed: ParsedDocument, line_counter: list[int]
) -> None:
    try:
        ocgs = doc.get_ocgs()
    except Exception:
        return
    off_names = [v.get("name", "") for v in ocgs.values() if not v.get("on", True)]
    if not off_names:
        return
    seen = {(s.page, s.text) for s in parsed.spans}
    present = _char_keys(doc)
    all_on = pymupdf.open(stream=data, filetype="pdf")
    with all_on:
        for cfg in all_on.layer_ui_configs():
            if not cfg.get("on"):
                all_on.set_layer_ui_config(cfg["number"], 0)
        for page in all_on:
            pno = page.number + 1
            raw = page.get_text("rawdict", flags=RAW_FLAGS, clip=pymupdf.INFINITE_RECT())
            for block in raw.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    line_counter[0] += 1
                    for sp in line.get("spans", []):
                        new_chars = [
                            ch
                            for ch in sp["chars"]
                            if (pno, ch["c"], _q(ch["origin"][0]), _q(ch["origin"][1])) not in present
                        ]
                        if not "".join(ch["c"] for ch in new_chars).strip():
                            continue
                        span = _span_from_raw(sp, new_chars, line["dir"], None, pno)
                        if (pno, span.text) in seen:
                            continue
                        span.source = SpanSource.HIDDEN_LAYER
                        span.layer = off_names[0] if len(off_names) == 1 else "OFF layer"
                        span.line_id = line_counter[0]
                        parsed.spans.append(span)


def _char_keys(doc: pymupdf.Document) -> set[tuple[int, str, int, int]]:
    keys: set[tuple[int, str, int, int]] = set()
    for page in doc:
        raw = page.get_text("rawdict", flags=RAW_FLAGS, clip=pymupdf.INFINITE_RECT())
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                for sp in line.get("spans", []):
                    for ch in sp["chars"]:
                        keys.add((page.number + 1, ch["c"], _q(ch["origin"][0]), _q(ch["origin"][1])))
    return keys


# ------------------------------------------------------------------------------------------------ metadata


def _metadata(doc: pymupdf.Document) -> dict[str, str]:
    meta = {
        k: str(v)
        for k, v in (doc.metadata or {}).items()
        if v and k in ("title", "author", "subject", "keywords", "creator", "producer")
    }
    try:
        xmp = doc.get_xml_metadata() or ""
    except Exception:
        xmp = ""
    if xmp:
        values = [
            v.strip()
            for v in _XMP_TEXT.findall(xmp)
            if v.strip() and not v.strip().startswith(("uuid:", "xmp.", "http"))
        ]
        if values:
            meta["xmp"] = " | ".join(dict.fromkeys(values))[:5000]
    return meta


def _body_font_size(spans: list[TextSpan]) -> float:
    weights: dict[float, int] = {}
    for s in spans:
        if s.source == SpanSource.BODY and s.text.strip() and s.effective_size >= 4:
            key = round(s.effective_size * 2) / 2
            weights[key] = weights.get(key, 0) + len(s.text.strip())
    return max(weights, key=lambda k: weights[k]) if weights else 10.0
