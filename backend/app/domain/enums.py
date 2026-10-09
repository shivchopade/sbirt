from __future__ import annotations

from enum import StrEnum


class ResumeStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"
    UNPARSEABLE = "UNPARSEABLE"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}


class SystemDecision(StrEnum):
    SHORTLISTED = "SHORTLISTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NOT_SHORTLISTED = "NOT_SHORTLISTED"
    FLAGGED = "FLAGGED"


class HumanDecision(StrEnum):
    APPROVED_FOR_INTERVIEW = "APPROVED_FOR_INTERVIEW"
    REJECTED = "REJECTED"
    IN_REVIEW = "IN_REVIEW"


class SpanSource(StrEnum):
    BODY = "body"
    HEADER = "header"
    FOOTER = "footer"
    TEXTBOX = "textbox"
    ANNOTATION = "annotation"
    COMMENT = "comment"
    ALT_TEXT = "alt_text"
    METADATA = "metadata"
    HIDDEN_LAYER = "hidden_layer"


# Text that is never part of the rendered page body (shown only on hover / in side panels / properties).
NON_PAGE_SOURCES = frozenset(
    {SpanSource.ANNOTATION, SpanSource.COMMENT, SpanSource.ALT_TEXT, SpanSource.METADATA}
)


class HiddenReason(StrEnum):
    LOW_CONTRAST = "LOW_CONTRAST"
    TINY_FONT = "TINY_FONT"
    LOW_OPACITY = "LOW_OPACITY"
    INVISIBLE_RENDER_MODE = "INVISIBLE_RENDER_MODE"
    OFF_PAGE = "OFF_PAGE"
    COVERED = "COVERED"
    HIDDEN_LAYER = "HIDDEN_LAYER"
    STACKED_TEXT = "STACKED_TEXT"
    DOCX_HIDDEN = "DOCX_HIDDEN"
    NOT_RENDERED_VISIBLY = "NOT_RENDERED_VISIBLY"
    ANNOTATION_ONLY = "ANNOTATION_ONLY"
    OCR_MISMATCH = "OCR_MISMATCH"


# When a span is hidden for several reasons, the flag is reported under the most specific one.
HIDDEN_REASON_PRIORITY: tuple[HiddenReason, ...] = (
    HiddenReason.HIDDEN_LAYER,
    HiddenReason.OFF_PAGE,
    HiddenReason.INVISIBLE_RENDER_MODE,
    HiddenReason.LOW_OPACITY,
    HiddenReason.TINY_FONT,
    HiddenReason.COVERED,
    HiddenReason.LOW_CONTRAST,
    HiddenReason.DOCX_HIDDEN,
    HiddenReason.STACKED_TEXT,
    HiddenReason.ANNOTATION_ONLY,
    HiddenReason.OCR_MISMATCH,
    HiddenReason.NOT_RENDERED_VISIBLY,
)

SUSPICIOUS_TINY_FONT = "TINY_FONT_SUSPICIOUS"
