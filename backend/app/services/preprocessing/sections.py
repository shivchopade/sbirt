"""Lines → sections → units (bullets / sentences).

A PDF line is merged with the previous one only when it is a genuine wrap: close underneath, not a bullet,
the previous line does not end a sentence and reaches (almost) the right edge of its column. DOCX paragraphs
are already complete. Long paragraphs are split into sentences with spaCy.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from app.domain.document import BBox, ParsedDocument, TextUnit
from app.domain.enums import NON_PAGE_SOURCES, SpanSource
from app.services.extraction.contact import EMAIL_RE, URL_RE, find_phone
from app.services.preprocessing.lexicons import section_headings
from app.services.preprocessing.normalize import normalize_text, strip_bullet

PERSONAL_RE = re.compile(
    r"^\s*(date of birth|d\.?\s?o\.?\s?b\.?|birth ?date|gender|sex|marital status|nationality|religion|caste|"
    r"father'?s name|mother'?s name|husband'?s name|spouse|age|passport(?: no\.?| number)?|"
    r"(?:permanent |current |residential )?address|blood group|height|weight)\s*[:\-]",
    re.I,
)
SENTENCE_END = (".", "!", "?", ";")
MAX_UNIT_WORDS = 80


@dataclass
class Line:
    text: str
    page: int
    bbox: BBox | None
    size: float
    bold: bool
    visible: bool
    source: SpanSource
    is_bullet: bool = False
    section: str = "header"
    is_heading: bool = False
    excluded: str | None = None
    hidden_reasons: list[str] = field(default_factory=list)


def build_lines(doc: ParsedDocument, counter: Counter[str]) -> list[Line]:
    lines: list[Line] = []
    for visible in (True, False):
        for page, _line_id, spans in doc.lines(visible=visible):
            raw = doc.join_spans(spans)
            if not raw.strip():
                continue
            text, bullet = strip_bullet(normalize_text(raw, counter))
            if not text:
                continue
            boxes = [s.bbox for s in spans if s.bbox is not None]
            bbox = (
                (
                    min(b[0] for b in boxes),
                    min(b[1] for b in boxes),
                    max(b[2] for b in boxes),
                    max(b[3] for b in boxes),
                )
                if boxes
                else None
            )
            content = [s for s in spans if s.text.strip()]
            lines.append(
                Line(
                    text=text,
                    page=page,
                    bbox=bbox,
                    size=max((s.effective_size for s in content), default=0.0),
                    bold=bool(content) and all(s.bold for s in content),
                    visible=visible,
                    source=spans[0].source,
                    is_bullet=bullet,
                    hidden_reasons=sorted({str(r) for s in spans for r in s.hidden_reasons}),
                )
            )
    for span in doc.spans:  # comments, alt text, annotations: never part of the page body
        if span.source in NON_PAGE_SOURCES and span.source != SpanSource.METADATA and span.text.strip():
            lines.append(
                Line(
                    text=normalize_text(span.text, counter),
                    page=span.page,
                    bbox=span.bbox,
                    size=span.effective_size,
                    bold=False,
                    visible=span.visible,
                    source=span.source,
                    section="non_page",
                    hidden_reasons=[str(r) for r in span.hidden_reasons],
                )
            )
    return lines


def _heading_key(text: str) -> str:
    key = re.sub(r"[^a-z& ]", " ", text.lower()).replace("&", "and")
    return " ".join(key.split())


def detect_heading(line: Line, body_size: float, current: str) -> str | None:
    text = line.text.strip()
    if not text or len(text) > 48 or len(text.split()) > 5 or text.endswith((".", ",")):
        return None
    if text.endswith(":") and len(text.split()) > 3:
        return None
    key = _heading_key(text.rstrip(":"))
    if not key:
        return None
    strong = (text.isupper() and len(key) > 3) or (line.size and line.size >= body_size * 1.1)
    styled = strong or line.bold or text.endswith(":")
    headings = section_headings()
    section = headings.get(key)
    if section is None and styled:
        match = process.extractOne(key, list(headings), scorer=fuzz.ratio, score_cutoff=88)
        section = headings[match[0]] if match else None
    if section is not None:
        # bold sub-labels inside a skills list ("Languages", "Tools") are not new sections
        if current == "skills" and not strong and section != "skills":
            return None
        return section if (styled or len(key.split()) <= 3) else None
    if strong and line.bold and not any(ch.isdigit() for ch in text) and line.visible:
        return "other"  # a styled heading of an unknown section
    return None


def assign_sections(lines: list[Line], body_size: float, candidate_name: str | None) -> None:
    section = "header"
    name_key = " ".join((candidate_name or "").lower().split())
    for line in lines:
        if line.source in NON_PAGE_SOURCES:
            continue
        lowered = " ".join(line.text.lower().split())
        if line.visible and name_key and lowered == name_key:
            line.section, line.excluded = section, "contact"  # the candidate's name is not a heading
            continue
        if not line.visible:
            line.section = "hidden"
        else:
            heading = detect_heading(line, body_size, section)
            if heading:
                section = heading
                line.section, line.is_heading = heading, True
                continue
            line.section = section
        if PERSONAL_RE.match(line.text) or line.section == "personal_details":
            line.excluded = "personal_details"
        elif (
            EMAIL_RE.search(line.text)
            or find_phone(line.text)
            or (URL_RE.search(line.text) and len(line.text.split()) <= 6)
            or (name_key and lowered == name_key)
        ):
            line.excluded = "contact"
        elif line.section in ("references", "declaration"):
            line.excluded = line.section


def _column_right_edges(lines: list[Line]) -> dict[tuple[int, int], float]:
    edges: dict[tuple[int, int], float] = defaultdict(float)
    for line in lines:
        if line.bbox is not None:
            key = (line.page, int(line.bbox[0] // 30))
            edges[key] = max(edges[key], line.bbox[2])
    return edges


def _can_merge(group: list[Line], line: Line, edges: dict[tuple[int, int], float], file_type: str) -> bool:
    prev = group[-1]
    if file_type != "pdf" or line.is_bullet or line.is_heading or prev.is_heading:
        return False
    if (prev.visible, prev.source, prev.section, prev.excluded) != (
        line.visible,
        line.source,
        line.section,
        line.excluded,
    ):
        return False
    if prev.bbox is None or line.bbox is None or prev.page != line.page:
        return False
    if prev.text.endswith(SENTENCE_END) and not line.text[:1].islower():
        return False
    height = max(prev.bbox[3] - prev.bbox[1], 1.0)
    gap = line.bbox[1] - prev.bbox[3]
    if gap > 0.9 * height or gap < -0.5 * height:
        return False
    first = group[0]
    right_edge = max(
        edges.get((first.page, int(first.bbox[0] // 30)), 0.0),
        edges.get((prev.page, int(prev.bbox[0] // 30)), 0.0),
    )  # type: ignore[index]
    width = right_edge - first.bbox[0]  # type: ignore[index]
    return width > 0 and (right_edge - prev.bbox[2]) <= 0.22 * width


def _join(lines: list[Line]) -> str:
    out = ""
    for line in lines:
        if out.endswith("-") and line.text[:1].islower():
            out = out[:-1] + line.text
        else:
            out = f"{out} {line.text}" if out else line.text
    return " ".join(out.split())


def _chunk(words: list[str], size: int) -> list[str]:
    return [" ".join(words[i : i + size]) for i in range(0, len(words), size)]


def segment(lines: list[Line], file_type: str, split_sentences: Callable[[str], list[str]]) -> list[TextUnit]:
    edges = _column_right_edges(lines)
    groups: list[list[Line]] = []
    for line in lines:
        if groups and _can_merge(groups[-1], line, edges, file_type):
            groups[-1].append(line)
        else:
            groups.append([line])

    units: list[TextUnit] = []
    for group in groups:
        first = group[0]
        text = _join(group)
        if first.is_heading:
            pieces = [text]
        else:
            pieces = split_sentences(text) if len(text.split()) > 25 or text.count(". ") >= 1 else [text]
        regions = [(ln.page, ln.bbox) for ln in group if ln.bbox is not None]
        bbox = None
        if regions:
            same_page = [b for p, b in regions if p == first.page]
            bbox = (
                min(b[0] for b in same_page),
                min(b[1] for b in same_page),
                max(b[2] for b in same_page),
                max(b[3] for b in same_page),
            )
        for piece in pieces:
            words = piece.split()
            for chunk in _chunk(words, MAX_UNIT_WORDS) if len(words) > MAX_UNIT_WORDS else [piece]:
                if not chunk.strip():
                    continue
                units.append(
                    TextUnit(
                        index=len(units),
                        text=chunk.strip(),
                        section=first.section,
                        visible=first.visible,
                        source=first.source,
                        page=first.page or None,
                        bbox=bbox,
                        regions=regions,
                        word_count=len(chunk.split()),
                        is_heading=first.is_heading,
                        excluded=first.excluded,
                        hidden_reasons=sorted({r for ln in group for r in ln.hidden_reasons}),
                    )
                )
    return units


def normalized_key(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())
