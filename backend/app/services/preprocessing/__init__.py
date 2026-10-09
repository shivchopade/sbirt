"""Preprocessing entry point: ParsedDocument → TextUnits (sections, lexical text, skills, PII-masked text)."""

from __future__ import annotations

import re
from collections import Counter

from app.domain.document import ParsedDocument, TextUnit
from app.services.preprocessing.nlp import NlpEngine, TextAnalysis
from app.services.preprocessing.sections import assign_sections, build_lines, segment

_LABEL_LIST = re.compile(r"^[A-Za-z][A-Za-z &/+\-]{1,30}:\s*\S")
_SEPARATORS = re.compile(r"[,|;/•·]")


def preprocess_document(
    doc: ParsedDocument, nlp: NlpEngine, *, candidate_name: str | None = None
) -> list[TextUnit]:
    counter: Counter[str] = Counter()
    lines = build_lines(doc, counter)
    doc.unicode_stats = dict(counter)
    assign_sections(lines, doc.body_font_size, candidate_name)
    units = segment(lines, doc.file_type, nlp.sentences)
    analyses = nlp.analyze([u.text for u in units], mask_names=(candidate_name,) if candidate_name else ())
    for unit, analysis in zip(units, analyses, strict=True):
        unit.lexical = analysis.lexical
        unit.skills = set(analysis.skills)
        unit.has_verb = analysis.has_verb
        unit.masked = analysis.masked
        unit.is_skill_list = _is_skill_list(unit, analysis)
    doc.units = units
    return units


def _is_skill_list(unit: TextUnit, analysis: TextAnalysis) -> bool:
    if unit.section == "skills":
        return True
    items = [p.strip() for p in _SEPARATORS.split(unit.text.split(":", 1)[-1]) if p.strip()]
    if len(items) >= 4 and len(analysis.skills) >= 0.5 * len(items):
        return True
    return bool(_LABEL_LIST.match(unit.text)) and len(analysis.skills) >= 2 and not analysis.has_verb


def sbert_text(unit: TextUnit, mode: str) -> str:
    if mode == "stopword_removed":
        return " ".join(t.removeprefix("skill:").replace("_", " ") for t in unit.lexical) or unit.masked
    return unit.masked or unit.text
