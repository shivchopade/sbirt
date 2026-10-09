"""Candidate contact details for the future scheduling agent. Only *visible* text is used."""

from __future__ import annotations

import re

from app.domain.document import ContactInfo, ParsedDocument, TextSpan
from app.domain.enums import SpanSource

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# +91 98765 43210, +91-98765-43210, 098765 43210, (022) 2345 6789, +1 (555) 123-4567 ...
PHONE_RE = re.compile(
    r"(?<![\w/])(\+?\d{1,3}[\s.\-]?)?(\(?\d{2,5}\)?[\s.\-]?)?\d{3,5}[\s.\-]?\d{3,5}(?![\w/])"
)
URL_RE = re.compile(r"(?:https?://|www\.)\S+|(?:linkedin\.com|github\.com|gitlab\.com)/\S+", re.I)
_HEADING_WORDS = {
    "resume",
    "curriculum",
    "vitae",
    "cv",
    "profile",
    "summary",
    "contact",
    "skills",
    "experience",
}


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10 or len(digits) > 13:
        return None
    plus = raw.strip().startswith("+")
    if len(digits) == 12 and digits.startswith("91"):
        return f"+91 {digits[2:7]} {digits[7:]}"
    if len(digits) == 11 and digits.startswith("0"):
        return f"+91 {digits[1:6]} {digits[6:]}"
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91 {digits[:5]} {digits[5:]}"
    return ("+" if plus else "") + digits


def find_phone(text: str) -> str | None:
    for m in PHONE_RE.finditer(text):
        candidate = m.group(0)
        if re.search(r"\b(19|20)\d{2}\s*[-–]\s*(19|20)\d{2}\b", candidate):
            continue  # a year range, not a phone number
        phone = normalize_phone(candidate)
        if phone:
            return phone
    return None


def extract_contact(doc: ParsedDocument) -> ContactInfo:
    visible_lines = [
        (page, spans)
        for page, _, spans in doc.lines(visible=True)
        if spans and spans[0].source in (SpanSource.BODY, SpanSource.TEXTBOX, SpanSource.HEADER)
    ]
    text = "\n".join(doc.join_spans(spans) for _, spans in visible_lines)
    info = ContactInfo(links=list(dict.fromkeys(doc.contact.links)))
    emails = EMAIL_RE.findall(text) or [
        link.removeprefix("mailto:") for link in info.links if link.startswith("mailto:")
    ]
    info.email = emails[0].lower() if emails else None
    info.phone = find_phone(text)
    for url in URL_RE.findall(text):
        url = url.rstrip(".,;)")
        if url not in info.links:
            info.links.append(url)
    info.links = [link for link in info.links if not link.startswith("mailto:")][:10]
    info.name = _guess_name(doc, visible_lines)
    return info


def _guess_name(doc: ParsedDocument, lines: list[tuple[int, list[TextSpan]]]) -> str | None:
    candidates: list[tuple[float, int, str]] = []
    first_page_lines = [(i, spans) for i, (page, spans) in enumerate(lines) if page in (0, 1)][:12]
    page_h = doc.page_sizes[0][1] if doc.page_sizes else 842.0
    merged: list[tuple[int, list[TextSpan], str]] = []
    for order, spans in first_page_lines:
        line = doc.join_spans(spans)
        size = max(s.effective_size for s in spans)
        prev = merged[-1] if merged else None
        # a large name wrapped onto two lines ("AARAV" / "SHARMA" in a narrow sidebar)
        if (
            prev is not None
            and len(line.split()) <= 2
            and len(prev[2].split()) <= 2
            and abs(max(s.effective_size for s in prev[1]) - size) < 0.5
            and size >= doc.body_font_size * 1.3
        ):
            merged[-1] = (prev[0], prev[1] + spans, f"{prev[2]} {line}")
        else:
            merged.append((order, spans, line))
    for order, spans, line in merged:
        if not _looks_like_name(line):
            continue
        top = min((s.bbox[1] for s in spans if s.bbox), default=0.0)
        if doc.file_type == "pdf" and top > page_h * 0.35:
            continue
        size = max(s.effective_size for s in spans)
        candidates.append((size, -order, line))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return _title_case(candidates[0][2])


def _looks_like_name(line: str) -> bool:
    if EMAIL_RE.search(line) or URL_RE.search(line) or any(ch.isdigit() for ch in line):
        return False
    words = [w for w in re.split(r"\s+", line.strip()) if w]
    if not 2 <= len(words) <= 5:
        return False
    if any(w.lower().strip(".:") in _HEADING_WORDS for w in words):
        return False
    return all(re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*", w) for w in words)


def _title_case(name: str) -> str:
    return " ".join(w if not w.isupper() else w.capitalize() for w in name.split())
