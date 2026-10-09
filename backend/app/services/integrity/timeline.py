from __future__ import annotations

import re
from datetime import date, timedelta

from app.domain.document import ParsedDocument, TextUnit
from app.domain.enums import Severity
from app.domain.flags import Evidence, Flag, snippet
from app.domain.job import JobContext
from app.services.integrity.base import first_location, regions_of, register

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
    )
}
_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_POINT = rf"(?:{_MONTH}\.?\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
_OPEN = r"(?:present|current|now|ongoing|till date|to date|today)"
RANGE_RE = re.compile(rf"(?P<start>{_POINT})\s*(?:-|to|until)\s*(?P<end>{_POINT}|{_OPEN})", re.I)
EXPECTED_RE = re.compile(
    r"\b(expected|anticipated|pursuing|ongoing|present|current|upcoming|graduating|will graduate)\b", re.I
)


def parse_point(text: str, *, end: bool = False) -> date | None:
    text = text.strip().lower().rstrip(".")
    m = re.fullmatch(rf"({_MONTH})\.?\s+(\d{{4}})", text)
    if m:
        return date(int(m.group(2)), _MONTHS[m.group(1)[:3]], 1)
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", text)
    if m and 1 <= int(m.group(1)) <= 12:
        return date(int(m.group(2)), int(m.group(1)), 1)
    m = re.fullmatch(r"(\d{4})", text)
    if m and 1950 <= int(m.group(1)) <= 2100:
        return date(int(m.group(1)), 12 if end else 1, 1)
    return None


@register
class TimelineDetector:
    """Catches impossible dates that suggest a fabricated history: a job or degree that ends before it starts,
    and dates in the future that are not marked "expected" / "present" / "pursuing". Low severity — usually a
    typo, sometimes an invented timeline; either way the reviewer should look."""

    name = "timeline"

    def detect(self, doc: ParsedDocument, ctx: JobContext) -> list[Flag]:
        today = ctx.cache.get("today") or date.today()
        horizon = today + timedelta(days=ctx.settings.integrity.timeline_future_grace_days)
        backwards: list[tuple[TextUnit, str]] = []
        future: list[tuple[TextUnit, str]] = []
        for unit in doc.units:
            if not unit.visible:
                continue
            for m in RANGE_RE.finditer(unit.text):
                start = parse_point(m.group("start"))
                end_raw = m.group("end")
                end = None if re.fullmatch(_OPEN, end_raw, re.I) else parse_point(end_raw, end=True)
                if start and end and end < start:
                    backwards.append((unit, m.group(0)))
                end_from = None if end is None else parse_point(end_raw)  # first day of the end period
                in_future = (start is not None and start > horizon) or (
                    end_from is not None and end_from > horizon
                )
                if in_future and not EXPECTED_RE.search(unit.text):
                    future.append((unit, m.group(0)))
        flags: list[Flag] = []
        for code, found, message in (
            ("TIMELINE_INCONSISTENT", backwards, "date range(s) end before they start"),
            ("TIMELINE_FUTURE_DATE", future, "date(s) in the future without 'expected' / 'present'"),
        ):
            if found:
                units = [u for u, _ in found]
                page, bbox = first_location(units)
                flags.append(
                    Flag(
                        code=code,
                        severity=Severity.LOW,
                        detector=self.name,
                        message=f"{len(found)} {message}: {', '.join(r for _, r in found[:3])}",
                        evidence=Evidence(
                            page=page,
                            bbox=bbox,
                            snippet=snippet(units[0].text),
                            measured_value=float(len(found)),
                            threshold=1.0,
                            location="visible",
                            regions=regions_of(units),
                            details={"ranges": [r for _, r in found], "today": today.isoformat()},
                        ),
                    )
                )
        return flags
