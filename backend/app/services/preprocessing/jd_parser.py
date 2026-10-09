"""Job description → requirement units, weighted skills and JD keywords.

* "Requirements / Must have / Responsibilities" units weigh 1.0, "Nice to have / Preferred" 0.5
  (also per line: "... is a plus", "(preferred)").
* Boilerplate (about us, benefits, equal-opportunity text) is dropped from requirements but kept in the
  sentence list used for copy detection.
* A JD without any recognisable headings is treated as one block of requirements.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz, process

from app.core.config import ScoringSettings
from app.domain.job import ParsedJob, Requirement, SkillRequirement
from app.services.preprocessing.lexicons import generic_terms, jd_section_headings
from app.services.preprocessing.nlp import NlpEngine
from app.services.preprocessing.normalize import normalize_text, strip_bullet

NICE_RE = re.compile(
    r"\b(nice to have|good to have|is a plus|are a plus|a plus|bonus|preferred|desirable|added advantage|an advantage)\b",
    re.I,
)
BOILERPLATE_RE = re.compile(
    r"\b(equal opportunity|eeo\b|e-verify|regardless of (race|gender|religion|age)|reasonable accommodation|"
    r"health insurance|paid time off|competitive salary|competitive compensation|how to apply|apply now|"
    r"send (your|us your) (cv|resume)|visa sponsorship)\b",
    re.I,
)


def _heading_kind(text: str) -> str | None:
    clean = text.strip().rstrip(":").strip()
    if not clean or len(clean.split()) > 6 or clean.endswith("."):
        return None
    key = " ".join(re.sub(r"[^a-z&' ]", " ", clean.lower()).replace("&", "and").split())
    headings = jd_section_headings()
    if key in headings:
        return headings[key]
    match = process.extractOne(key, list(headings), scorer=fuzz.ratio, score_cutoff=90)
    return headings[match[0]] if match else None


def parse_jd(title: str, jd_text: str, nlp: NlpEngine, scoring: ScoringSettings) -> ParsedJob:
    raw_lines = [normalize_text(line) for line in jd_text.replace("\r", "").split("\n")]
    lines = [ln for ln in raw_lines if ln]
    has_headings = any(_heading_kind(strip_bullet(ln)[0]) for ln in lines)

    sentences: list[str] = []
    content: list[str] = []
    candidates: list[tuple[str, str, str]] = []  # (text, kind, section heading)
    kind, section = ("intro" if has_headings else "general"), ""
    for idx, line in enumerate(lines):
        text, _bullet = strip_bullet(line)
        heading = _heading_kind(text)
        if heading:
            kind, section = heading, text.rstrip(":").strip()
            continue
        pieces = nlp.sentences(text) if len(text.split()) > 30 or ". " in text else [text]
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            sentences.append(piece)
            if kind == "boilerplate" or BOILERPLATE_RE.search(piece):
                continue
            content.append(piece)
            if idx == 0 and len(piece.split()) <= 12 and not piece.endswith("."):
                continue  # the title line
            if kind in ("responsibility", "must", "nice", "general") and len(piece.split()) >= 3:
                candidates.append((piece, kind, section))

    if not candidates:  # no usable sections: every non-boilerplate sentence is a requirement
        candidates = [(s, "general", "") for s in content if len(s.split()) >= 3]

    requirements = [
        Requirement(
            index=i,
            text=text,
            weight=scoring.nice_to_have_weight
            if (k == "nice" or NICE_RE.search(text))
            else scoring.must_have_weight,
            section=sec or k,
            kind="nice"
            if (k == "nice" or NICE_RE.search(text))
            else ("must" if k in ("must", "general") else k),
        )
        for i, (text, k, sec) in enumerate(candidates)
    ]

    for req in requirements:
        req.parts = nlp.clauses(req.text)

    analyses = nlp.analyze([r.text for r in requirements] + [title])
    skill_weights: dict[str, float] = {}
    keywords: set[str] = set()
    generic = generic_terms()
    for req, analysis in zip([*requirements, None], analyses, strict=True):
        weight = req.weight if req is not None else scoring.must_have_weight
        for skill in analysis.skills:
            skill_weights[skill] = max(skill_weights.get(skill, 0.0), weight)
            keywords.add(nlp.catalog[skill].token)
        for tok, pos in zip(analysis.lexical, analysis.lexical_pos, strict=True):
            if tok.startswith("skill:") or pos not in ("NOUN", "PROPN"):
                continue
            if len(tok) >= 3 and tok not in generic and tok.isascii():
                keywords.add(tok)
    skills = sorted(
        (
            SkillRequirement(name=name, display=nlp.catalog.display(name), weight=w)
            for name, w in skill_weights.items()
        ),
        key=lambda s: (-s.weight, s.display.lower()),
    )
    return ParsedJob(
        title=title,
        jd_text=jd_text,
        sentences=sentences,
        requirements=requirements,
        skills=skills,
        keywords=keywords,
        content_sentences=content,
    )
