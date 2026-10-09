"""Internal document model: what extraction produces and every later stage reads."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from app.domain.enums import NON_PAGE_SOURCES, HiddenReason, SpanSource

if TYPE_CHECKING:
    import numpy as np

BBox = tuple[float, float, float, float]
RGB = tuple[int, int, int]

_WS = re.compile(r"\s+")


@dataclass(slots=True)
class TextSpan:
    text: str
    page: int  # 1-based; 0 for text that is not on a page (comments, alt text, metadata)
    bbox: BBox | None
    font: str = ""
    declared_size: float = 0.0
    effective_size: float = 0.0
    color_rgb: RGB = (0, 0, 0)
    opacity: float = 1.0
    render_mode: int = 0  # PDF text render mode: 0 fill, 1 stroke, 2 fill+stroke, 3 invisible, ...
    background_rgb: RGB | None = None
    source: SpanSource = SpanSource.BODY
    visible: bool = True
    hidden_reasons: list[HiddenReason] = field(default_factory=list)
    suspicious_reasons: list[str] = field(default_factory=list)
    measurements: dict[str, float] = field(default_factory=dict)
    seqno: int = -1  # draw order on the page (PDF)
    bold: bool = False
    italic: bool = False
    line_id: int = -1  # spans sharing a line_id form one visual line (PDF) / paragraph (DOCX)
    block_id: int = -1
    layer: str | None = None
    confidence: str | None = None  # "high" when a rule and the independent pixel check agree

    def hide(self, reason: HiddenReason, measured: float | None = None) -> None:
        if reason not in self.hidden_reasons:
            self.hidden_reasons.append(reason)
        if measured is not None:
            self.measurements[str(reason)] = round(float(measured), 4)
        self.visible = False

    @property
    def is_blank(self) -> bool:
        return not self.text.strip()


@dataclass(slots=True)
class ImageBox:
    page: int
    bbox: BBox
    seqno: int
    has_mask: bool = False


@dataclass(slots=True)
class ShapeBox:
    page: int
    bbox: BBox
    seqno: int
    fill_rgb: RGB | None
    fill_opacity: float = 1.0


@dataclass(slots=True)
class DrawOp:
    page: int
    seqno: int
    kind: str
    bbox: BBox


@dataclass
class SecurityReport:
    encrypted: bool = False
    needs_password: bool = False
    javascript: bool = False
    launch_action: bool = False
    open_action: str | None = None  # suspicious OpenAction / AA action type (GoTo destinations are ignored)
    embedded_files: int = 0
    macros: bool = False
    external_template: str | None = None
    zip_bomb: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def malicious_pdf(self) -> bool:
        return self.javascript or self.launch_action or bool(self.open_action) or self.embedded_files > 0


@dataclass
class ContactInfo:
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    links: list[str] = field(default_factory=list)


@dataclass
class TextUnit:
    """A sentence / bullet: the unit that gets embedded, matched and checked."""

    index: int
    text: str  # normalized natural text
    masked: str = ""  # PII-masked natural text (SBERT input)
    lexical: list[str] = field(default_factory=list)  # stopword-free lemmas, skills merged as single tokens
    skills: set[str] = field(default_factory=set)
    section: str = "other"
    visible: bool = True
    source: SpanSource = SpanSource.BODY
    page: int | None = None
    bbox: BBox | None = None
    regions: list[tuple[int, BBox]] = field(default_factory=list)
    word_count: int = 0
    has_verb: bool = False
    is_heading: bool = False
    is_skill_list: bool = False
    excluded: str | None = None  # "personal_details" / "contact" — never scored
    is_jd_copy: bool = False
    jd_match: dict[str, Any] | None = None
    hidden_reasons: list[str] = field(default_factory=list)


@dataclass
class ParsedDocument:
    file_name: str
    file_type: Literal["pdf", "docx"]
    sha256: str
    size_bytes: int
    page_count: int = 0
    page_sizes: list[tuple[float, float]] = field(default_factory=list)
    spans: list[TextSpan] = field(default_factory=list)
    images: list[ImageBox] = field(default_factory=list)
    shapes: list[ShapeBox] = field(default_factory=list)
    draw_log: list[DrawOp] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    security: SecurityReport = field(default_factory=SecurityReport)
    contact: ContactInfo = field(default_factory=ContactInfo)
    warnings: list[str] = field(default_factory=list)
    renders: dict[int, np.ndarray] = field(default_factory=dict)  # page -> RGB uint8 array (transient)
    render_dpi: int = 150
    ocr_layer_pages: set[int] = field(default_factory=set)
    page_background: tuple[int, int, int] = (255, 255, 255)
    units: list[TextUnit] = field(default_factory=list)
    unicode_stats: dict[str, int] = field(default_factory=dict)
    body_font_size: float = 10.0

    # ---- text views -------------------------------------------------------------------------------
    def lines(
        self, *, visible: bool | None = None, page_only: bool = True
    ) -> list[tuple[int, int, list[TextSpan]]]:
        """Group spans into lines: [(page, line_id, spans)] in reading order.

        visible=True keeps only visible spans, False only hidden ones, None keeps everything.
        page_only drops comments / alt text / annotations / metadata.
        """
        out: list[tuple[int, int, list[TextSpan]]] = []
        index: dict[tuple[int, int, SpanSource], int] = {}
        for span in self.spans:
            if page_only and span.source in NON_PAGE_SOURCES:
                continue
            if visible is not None and span.visible != visible:
                continue
            key = (span.page, span.line_id, span.source)
            if key not in index:
                index[key] = len(out)
                out.append((span.page, span.line_id, []))
            out[index[key]][2].append(span)
        return out

    @staticmethod
    def join_spans(spans: list[TextSpan]) -> str:
        parts: list[str] = []
        prev: TextSpan | None = None
        for span in spans:
            text = span.text
            if (
                parts
                and prev is not None
                and not parts[-1].endswith((" ", "\t"))
                and not text.startswith((" ", "\t"))
            ):
                gap = (span.bbox[0] - prev.bbox[2]) if (span.bbox and prev.bbox) else 1.0
                if gap > 0.15 * max(span.effective_size or span.declared_size, 1.0):
                    parts.append(" ")
            parts.append(text)
            prev = span
        return _WS.sub(" ", "".join(parts)).strip()

    def text_of(self, *, visible: bool | None) -> str:
        return "\n".join(t for _, _, spans in self.lines(visible=visible) if (t := self.join_spans(spans)))

    @property
    def visible_text(self) -> str:
        return self.text_of(visible=True)

    @property
    def hidden_text(self) -> str:
        hidden = self.text_of(visible=False)
        non_page = "\n".join(
            s.text.strip()
            for s in self.spans
            if s.source in NON_PAGE_SOURCES - {SpanSource.METADATA} and s.text.strip()
        )
        return "\n".join(t for t in (hidden, non_page) if t)

    @property
    def raw_text(self) -> str:
        return self.text_of(visible=None)

    @property
    def hidden_spans(self) -> list[TextSpan]:
        return [s for s in self.spans if not s.visible and not s.is_blank]
