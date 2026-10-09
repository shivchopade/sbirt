"""Skill catalogue (skills.json), spaCy PhraseMatcher matching and set-based skill scoring.

Matching happens on the tokenised text *before* stopword removal so "Go", "R", "C++" or "IT" survive.
Ambiguous names (Go, R, C, React, Spring, ...) are matched case-sensitively by their display spelling only;
everything else case-insensitively, including aliases (js → javascript, postgres → postgresql, ml → machine
learning). Scoring is set-based: repeating a skill adds nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from app.services.preprocessing.lexicons import skills_data

if TYPE_CHECKING:
    from spacy.language import Language
    from spacy.tokens import Doc, Span

    from app.domain.job import SkillRequirement
    from app.services.scoring.embedder import Embedder


@dataclass(frozen=True)
class Skill:
    name: str
    display: str
    category: str
    aliases: tuple[str, ...]
    ambiguous: bool = False
    implies: tuple[str, ...] = ()

    @property
    def token(self) -> str:
        """Single lexical token used in the lexical text, e.g. machine_learning."""
        return "skill:" + self.name.replace(" ", "_")

    @property
    def multi_word(self) -> bool:
        return len(self.display.split()) > 1 and not self.ambiguous


class SkillCatalog:
    def __init__(self, data: dict[str, Any] | None = None):
        data = data or skills_data()
        self.skills: dict[str, Skill] = {}
        for raw in data["skills"]:
            skill = Skill(
                name=raw["name"],
                display=raw["display"],
                category=raw.get("category", "other"),
                aliases=tuple(raw.get("aliases", [])),
                ambiguous=bool(raw.get("ambiguous", False)),
                implies=tuple(raw.get("implies", [])),
            )
            self.skills[skill.name] = skill

    def __getitem__(self, name: str) -> Skill:
        return self.skills[name]

    def __contains__(self, name: object) -> bool:
        return name in self.skills

    def __iter__(self):
        return iter(self.skills.values())

    def display(self, name: str) -> str:
        return self.skills[name].display if name in self.skills else name

    def implied(self, found: set[str]) -> dict[str, str]:
        """Broader skills demonstrated by specific ones: {implied_skill: source_skill}."""
        out: dict[str, str] = {}
        frontier = list(found)
        while frontier:
            src = frontier.pop()
            for target in self.skills[src].implies if src in self.skills else ():
                if target not in found and target not in out:
                    out[target] = src
                    frontier.append(target)
        return out

    def forms(self, skill: Skill) -> tuple[list[str], list[str]]:
        """(case-insensitive forms, case-sensitive forms)."""
        lower = list(skill.aliases)
        exact: list[str] = []
        if skill.ambiguous:
            exact.append(skill.display)
        else:
            lower += [skill.name, skill.display]
        plural = [
            f + "s" for f in lower if f[-1:].isalpha() and not f.endswith("s") and len(f) > 3 and " " in f
        ]
        return sorted(set(lower + plural), key=str.lower), exact


class SkillMatcher:
    def __init__(self, nlp: Language, catalog: SkillCatalog):
        from spacy.matcher import PhraseMatcher

        self.catalog = catalog
        self.lower = PhraseMatcher(nlp.vocab, attr="LOWER")
        self.exact = PhraseMatcher(nlp.vocab, attr="ORTH")
        for skill in catalog:
            lower, exact = catalog.forms(skill)
            if lower:
                self.lower.add(skill.name, [nlp.make_doc(f) for f in lower])
            if exact:
                self.exact.add(skill.name, [nlp.make_doc(f) for f in exact])
        self.vocab = nlp.vocab

    def find(self, doc: Doc) -> list[Span]:
        from spacy.tokens import Span
        from spacy.util import filter_spans

        spans = [
            Span(doc, s, e, label=self.vocab.strings[m]) for m, s, e in [*self.lower(doc), *self.exact(doc)]
        ]
        return filter_spans(spans)


# --------------------------------------------------------------------------------------------- scoring


@dataclass
class SkillDetail:
    skill: str
    display: str
    weight: float
    matched: bool
    method: str | None = None  # lexical | implied | semantic
    evidence: str | None = None
    similarity: float | None = None


@dataclass
class SkillScore:
    score: float  # 0–100
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    details: list[SkillDetail] = field(default_factory=list)
    applicable: bool = True


def score_skills(
    required: list[SkillRequirement],
    found: dict[str, str],  # canonical skill → evidence line
    catalog: SkillCatalog,
    *,
    unit_texts: list[str],
    unit_vectors: np.ndarray,
    embedder: Embedder,
    semantic_threshold: float,
) -> SkillScore:
    if not required:
        return SkillScore(score=0.0, applicable=False)
    implied = catalog.implied(set(found))
    details: list[SkillDetail] = []
    pending: list[int] = []
    for req in required:
        detail = SkillDetail(skill=req.name, display=req.display, weight=req.weight, matched=False)
        if req.name in found:
            detail.matched, detail.method, detail.evidence = True, "lexical", found[req.name]
        elif req.name in implied:
            src = implied[req.name]
            detail.matched, detail.method = True, "implied"
            detail.evidence = f"via {catalog.display(src)}: {found.get(src, '')}".strip()
        elif req.name in catalog and catalog[req.name].multi_word and len(unit_texts):
            pending.append(len(details))
        details.append(detail)
    if pending and len(unit_texts):
        vectors = embedder.encode([details[i].display for i in pending])
        sims = vectors @ unit_vectors.T
        for row, i in enumerate(pending):
            j = int(np.argmax(sims[row]))
            best = float(sims[row, j])
            details[i].similarity = round(best, 4)
            if best >= semantic_threshold:
                details[i].matched, details[i].method, details[i].evidence = True, "semantic", unit_texts[j]
    total = sum(d.weight for d in details)
    got = sum(d.weight for d in details if d.matched)
    return SkillScore(
        score=round(100.0 * got / total, 2) if total else 0.0,
        matched=[d.display for d in details if d.matched],
        missing=[d.display for d in details if not d.matched],
        details=details,
    )
