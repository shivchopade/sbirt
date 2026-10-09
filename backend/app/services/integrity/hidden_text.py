from __future__ import annotations

from collections import defaultdict

from rapidfuzz import fuzz

from app.domain.document import BBox, ParsedDocument, TextSpan, TextUnit
from app.domain.enums import (
    HIDDEN_REASON_PRIORITY,
    NON_PAGE_SOURCES,
    SUSPICIOUS_TINY_FONT,
    HiddenReason,
    Severity,
    SpanSource,
)
from app.domain.flags import Evidence, Flag, snippet
from app.domain.job import JobContext
from app.services.integrity.base import jd_keyword_hits, register
from app.services.preprocessing.normalize import canonical

_DESCRIBE = {
    HiddenReason.LOW_CONTRAST: "text with almost no contrast against its background (e.g. white on white)",
    HiddenReason.TINY_FONT: "text in an unreadably small font",
    HiddenReason.LOW_OPACITY: "almost fully transparent text",
    HiddenReason.INVISIBLE_RENDER_MODE: "text drawn in the PDF 'invisible' render mode",
    HiddenReason.OFF_PAGE: "text placed outside the visible page",
    HiddenReason.COVERED: "text covered by a shape or image drawn on top of it",
    HiddenReason.HIDDEN_LAYER: "text inside a PDF layer that is switched off",
    HiddenReason.STACKED_TEXT: "text hidden underneath other text at the same position",
    HiddenReason.DOCX_HIDDEN: "hidden Word text",
    HiddenReason.NOT_RENDERED_VISIBLY: "text that does not show up in the rendered page (pixel check)",
    HiddenReason.ANNOTATION_ONLY: "text that exists only in a PDF comment/annotation",
    HiddenReason.OCR_MISMATCH: "text that OCR cannot find on the rendered page",
}
_DOCX_KINDS = {
    "vanish": "Word 'hidden' formatting",
    "same_color": "text coloured like its background",
    "tiny_font": "text in a font smaller than 4 pt",
    "comment": "text only present in a Word comment",
    "alt_text": "text only present in image alt text",
    "tracked_deletion": "deleted tracked-change text",
}


def _primary(span: TextSpan) -> HiddenReason:
    for reason in HIDDEN_REASON_PRIORITY:
        if reason in span.hidden_reasons:
            return reason
    return span.hidden_reasons[0]


def _union(boxes: list[BBox]) -> BBox | None:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


@register
class HiddenTextDetector:
    """Catches text a human reviewer cannot see but a parser reads: white-on-white / low-contrast text, tiny or
    squeezed fonts, transparent text, the invisible PDF render mode, off-page text, text covered by shapes or
    images, OFF layers, text stacked under other text, Word hidden runs, comments / alt text stuffing, and anything
    the independent pixel check finds not rendered. Escalates to critical when the hidden text contains JD
    sentences or JD keywords (classic "white-fonting")."""

    name = "hidden_text"

    def detect(self, doc: ParsedDocument, ctx: JobContext) -> list[Flag]:
        vis = ctx.settings.visibility
        integ = ctx.settings.integrity
        groups: dict[HiddenReason, list[TextSpan]] = defaultdict(list)
        for span in doc.hidden_spans:
            if span.source in NON_PAGE_SOURCES and not self._substantial(span, doc, ctx):
                continue
            groups[_primary(span)].append(span)

        flags: list[Flag] = []
        jd_sents = [
            (canonical(s), s) for s in ctx.parsed.sentences if len(s.split()) >= integ.jd_line_min_words
        ]
        for reason, spans in groups.items():
            text = " ".join(s.text.strip() for s in spans if s.text.strip())
            units = self._units_for(doc, reason, spans)
            keywords = jd_keyword_hits(units, ctx.parsed.keywords)
            copied = self._jd_sentences_in(text, jd_sents)
            critical = bool(copied) or len(keywords) >= integ.hidden_escalate_min_keywords
            measured, threshold = self._measure(reason, spans, vis)
            measure_note = None
            if (
                measured is None
            ):  # on/off conditions (OFF layer, Word "hidden", comments): report how much text
                measured, threshold, measure_note = (
                    float(len(text)),
                    1.0,
                    "hidden characters (any amount is hidden)",
                )
            first = next((s for s in spans if s.bbox is not None), spans[0])
            boxes = [s.bbox for s in spans if s.bbox is not None and s.page == first.page]
            label = self._label(reason, spans)
            suffix = " containing JD sentences" if copied else (" containing JD keywords" if critical else "")
            flags.append(
                Flag(
                    code=str(reason),
                    severity=Severity.CRITICAL if critical else Severity.HIGH,
                    detector=self.name,
                    message=f"{len(text)} characters of {label}{suffix}",
                    evidence=Evidence(
                        page=first.page or None,
                        bbox=_union(boxes),
                        snippet=snippet(text),
                        measured_value=measured,
                        threshold=threshold,
                        location="hidden",
                        regions=[{"page": s.page, "bbox": list(s.bbox)} for s in spans if s.bbox is not None][
                            :40
                        ],
                        details={
                            "spans": len(spans),
                            "confidence": "high" if any(s.confidence == "high" for s in spans) else "medium",
                            "jd_keywords": keywords[:20],
                            "jd_sentences": copied[:3],
                            "all_reasons": sorted({str(r) for s in spans for r in s.hidden_reasons}),
                            **({"measured_value_means": measure_note} if measure_note else {}),
                            **self._docx_position(doc, spans),
                            **(
                                {
                                    "docx_kind": sorted(
                                        {
                                            s.attrs.get("docx_hidden")
                                            for s in spans
                                            if s.attrs.get("docx_hidden")
                                        }
                                    )
                                }
                                if reason == HiddenReason.DOCX_HIDDEN
                                else {}
                            ),
                        },
                    ),
                )
            )
        flags.extend(self._suspicious_tiny(doc, vis))
        return flags

    # ---------------------------------------------------------------- helpers
    def _docx_position(self, doc: ParsedDocument, spans: list[TextSpan]) -> dict:
        """DOCX has no fixed layout: say where the text lives instead of a bounding box."""
        if doc.file_type != "docx":
            return {}
        first = spans[0]
        if first.source == SpanSource.COMMENT:
            return {
                "position": f"Word comment by {first.attrs.get('author') or 'unknown author'} (not shown on the page)"
            }
        if first.source == SpanSource.ALT_TEXT:
            return {"position": "image alt text (not shown on the page)"}
        return {"position": f"paragraph {first.line_id} of the document body (page {first.page} estimated)"}

    def _substantial(self, span: TextSpan, doc: ParsedDocument, ctx: JobContext) -> bool:
        """Comments, alt text and annotations are only flagged when they carry real content."""
        vis = ctx.settings.visibility
        if len(span.text.split()) >= vis.nonbody_min_words:
            return True
        units = [
            u for u in doc.units if u.source == span.source and u.text.strip() and u.text.strip() in span.text
        ]
        return len(jd_keyword_hits(units, ctx.parsed.keywords)) >= vis.nonbody_min_keywords

    def _units_for(self, doc: ParsedDocument, reason: HiddenReason, spans: list[TextSpan]) -> list[TextUnit]:
        texts = " ".join(s.text for s in spans)
        return [
            u
            for u in doc.units
            if not u.visible and (str(reason) in u.hidden_reasons or u.text[:40] in texts)
        ]

    def _jd_sentences_in(self, text: str, jd_sents: list[tuple[str, str]]) -> list[str]:
        key = canonical(text)
        return [orig for jd_key, orig in jd_sents if fuzz.partial_ratio(jd_key, key) >= 90]

    def _measure(self, reason: HiddenReason, spans: list[TextSpan], vis) -> tuple[float | None, float | None]:
        values = [s.measurements[str(reason)] for s in spans if str(reason) in s.measurements]
        table = {
            HiddenReason.LOW_CONTRAST: (min, vis.low_contrast_ratio),
            HiddenReason.TINY_FONT: (min, vis.tiny_font_hidden_pt),
            HiddenReason.LOW_OPACITY: (min, vis.low_opacity),
            HiddenReason.OFF_PAGE: (max, vis.off_page_min_outside),
            HiddenReason.COVERED: (max, vis.covered_min_fraction),
            HiddenReason.STACKED_TEXT: (max, vis.stacked_min_overlap),
            HiddenReason.NOT_RENDERED_VISIBLY: (min, vis.pixel_min_ink_fraction),
            HiddenReason.INVISIBLE_RENDER_MODE: (max, 3.0),
            HiddenReason.DOCX_HIDDEN: (min, None),
        }
        agg, threshold = table.get(reason, (None, None))
        if reason == HiddenReason.DOCX_HIDDEN:
            kinds = {s.attrs.get("docx_hidden") for s in spans}
            threshold = (
                vis.low_contrast_ratio
                if "same_color" in kinds
                else (vis.tiny_font_hidden_pt if "tiny_font" in kinds else None)
            )
        measured = round(agg(values), 4) if (agg and values) else None
        return measured, threshold

    def _label(self, reason: HiddenReason, spans: list[TextSpan]) -> str:
        if reason == HiddenReason.DOCX_HIDDEN:
            kinds = sorted({s.attrs.get("docx_hidden") for s in spans if s.attrs.get("docx_hidden")})
            return " / ".join(_DOCX_KINDS.get(k, k) for k in kinds) or _DESCRIBE[reason]
        return _DESCRIBE.get(reason, str(reason))

    def _suspicious_tiny(self, doc: ParsedDocument, vis) -> list[Flag]:
        spans = [s for s in doc.spans if s.visible and SUSPICIOUS_TINY_FONT in s.suspicious_reasons]
        if not spans:
            return []
        text = " ".join(s.text.strip() for s in spans)
        first = next((s for s in spans if s.bbox is not None), spans[0])
        size = min(s.measurements.get(SUSPICIOUS_TINY_FONT, s.effective_size) for s in spans)
        return [
            Flag(
                code="TINY_FONT",
                severity=Severity.MEDIUM,
                detector=self.name,
                message=f"{len(text)} characters in a {size:.1f} pt font — readable only when zoomed in",
                evidence=Evidence(
                    page=first.page or None,
                    bbox=first.bbox,
                    snippet=snippet(text),
                    measured_value=round(size, 2),
                    threshold=vis.tiny_font_suspicious_pt,
                    location="visible",
                    regions=[{"page": s.page, "bbox": list(s.bbox)} for s in spans if s.bbox is not None][
                        :40
                    ],
                    details={"suspicious_only": True, "spans": len(spans)},
                ),
            )
        ]
