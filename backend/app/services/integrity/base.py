"""Detector protocol + registry. Adding a new integrity check = one new file with a ``@register``-ed class."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from app.domain.document import BBox, ParsedDocument, TextUnit
from app.domain.enums import SpanSource
from app.domain.flags import Flag
from app.domain.job import JobContext

log = logging.getLogger(__name__)


@runtime_checkable
class Detector(Protocol):
    name: str

    def detect(self, doc: ParsedDocument, ctx: JobContext) -> list[Flag]: ...


_REGISTRY: list[type] = []


def register(cls: type) -> type:
    if cls not in _REGISTRY:
        _REGISTRY.append(cls)
    return cls


def all_detectors() -> list[Detector]:
    import app.services.integrity.detectors  # noqa: F401  (imports every detector module → registration)

    return [cls() for cls in _REGISTRY]


def run_detectors(doc: ParsedDocument, ctx: JobContext) -> list[Flag]:
    flags: list[Flag] = []
    for detector in all_detectors():
        try:
            flags.extend(detector.detect(doc, ctx))
        except Exception as exc:  # one broken detector must never break the whole analysis
            log.exception("detector failed", extra={"detector": detector.name})
            doc.warnings.append(f"detector {detector.name} failed: {exc.__class__.__name__}")
    if doc.file_type == "docx":
        _annotate_docx_positions(doc, flags)
    return flags


def _annotate_docx_positions(doc: ParsedDocument, flags: list[Flag]) -> None:
    """DOCX has no fixed layout, so text evidence names where the text lives instead of a bounding box."""
    for flag in flags:
        ev = flag.evidence
        if (
            ev.location not in ("visible", "hidden", "mixed")
            or ev.bbox is not None
            or "position" in ev.details
        ):
            continue
        unit = next(
            (u for u in doc.units if ev.snippet and u.text and ev.snippet.startswith(u.text[:40])), None
        )
        if unit is None:
            continue
        if unit.source == SpanSource.COMMENT:
            ev.details["position"] = "Word comment (not shown on the page)"
        elif unit.source == SpanSource.ALT_TEXT:
            ev.details["position"] = "image alt text (not shown on the page)"
        else:
            ev.details["position"] = (
                f"{unit.section} section, page {unit.page or 1} (estimated — DOCX has no fixed layout)"
            )
        if ev.page is None and unit.page:
            ev.page = unit.page


# ------------------------------------------------------------------------------------------ helpers


def regions_of(units: list[TextUnit], limit: int = 30) -> list[dict]:
    out: list[dict] = []
    for unit in units:
        for page, bbox in unit.regions:
            out.append({"page": page, "bbox": list(bbox)})
            if len(out) >= limit:
                return out
    return out


def first_location(units: list[TextUnit]) -> tuple[int | None, BBox | None]:
    for unit in units:
        if unit.page is not None and unit.bbox is not None:
            return unit.page, unit.bbox
    return (units[0].page if units else None), None


def location_of(units: list[TextUnit]) -> str:
    kinds = {"visible" if u.visible else "hidden" for u in units}
    return kinds.pop() if len(kinds) == 1 else "mixed"


def jd_keyword_hits(units: list[TextUnit], keywords: set[str]) -> list[str]:
    found: dict[str, None] = {}
    for unit in units:
        for tok in unit.lexical:
            if tok in keywords:
                found.setdefault(tok, None)
    return [k.removeprefix("skill:").replace("_", " ") for k in found]
