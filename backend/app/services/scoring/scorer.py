"""Clean vs raw scoring.

* clean_score — visible units minus verbatim-JD units (the official score)
* raw_score   — everything on the page, hidden text included (what a naive ATS would compute)
* gaming_delta = raw − clean; ≥ threshold → SCORE_INFLATION_ATTEMPT

Gaming resistance: units are embedded individually (MiniLM truncates long input) and aggregated; requirement
coverage uses max similarity; skills are a set; identical units are de-duplicated before mean pooling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from app.core.config import Settings
from app.domain.document import TextUnit
from app.domain.enums import NON_PAGE_SOURCES, Severity
from app.domain.flags import Evidence, Flag, snippet
from app.domain.job import ParsedJob
from app.services.preprocessing import sbert_text
from app.services.preprocessing.sections import normalized_key
from app.services.scoring.embedder import mean_vector
from app.services.scoring.requirements import RequirementMatch, calibrate, requirement_coverage
from app.services.scoring.skills import SkillScore, score_skills

if TYPE_CHECKING:
    from app.services.resources import Resources


@dataclass
class JobVectors:
    requirements: np.ndarray
    jd_mean: np.ndarray | None
    parts: list[np.ndarray | None] = field(default_factory=list)  # clause vectors per requirement


@dataclass
class ScoreResult:
    score: float
    requirement_coverage: float
    skill_match: float
    overall_similarity: float
    overall_raw: float
    requirements: list[RequirementMatch]
    skills: SkillScore
    weights: dict[str, float]
    units: list[TextUnit] = field(default_factory=list)
    resume_vector: np.ndarray | None = None


@dataclass
class ScoringOutcome:
    clean: ScoreResult
    raw: ScoreResult
    gaming_delta: float
    flags: list[Flag]
    unit_vectors: dict[int, np.ndarray]
    counts: dict[str, int]


def job_vectors(job: ParsedJob, resources: Resources) -> JobVectors:
    emb = resources.embedder
    reqs = (
        emb.encode([r.text for r in job.requirements])
        if job.requirements
        else np.zeros((0, emb.dim), dtype=np.float32)
    )
    parts = [emb.encode(r.parts) if r.parts else None for r in job.requirements]
    content = job.content_sentences or [r.text for r in job.requirements]
    return JobVectors(
        requirements=reqs, jd_mean=mean_vector(emb.encode(content)) if content else None, parts=parts
    )


def select_units(units: list[TextUnit], mode: str, min_words: int) -> list[TextUnit]:
    chosen: list[TextUnit] = []
    seen: set[str] = set()
    for unit in units:
        if unit.source in NON_PAGE_SOURCES or unit.is_heading or unit.excluded or unit.word_count < min_words:
            continue
        if mode == "clean" and (not unit.visible or unit.is_jd_copy):
            continue
        key = normalized_key(unit.text)
        if not key or key in seen:  # identical units count once
            continue
        seen.add(key)
        chosen.append(unit)
    return chosen


def score_units(
    units: list[TextUnit],
    vectors: np.ndarray,
    job: ParsedJob,
    jv: JobVectors,
    resources: Resources,
    settings: Settings,
) -> ScoreResult:
    sc = settings.scoring
    req_score, matches = requirement_coverage(
        job.requirements,
        jv.requirements,
        units,
        vectors,
        floor=sc.calib_floor,
        ceiling=sc.calib_ceiling,
        met_threshold=sc.requirement_met,
        part_vectors=jv.parts,
    )
    found: dict[str, str] = {}
    for unit in units:
        for skill in unit.skills:
            found.setdefault(skill, unit.text)
    skills = score_skills(
        job.skills,
        found,
        resources.catalog,
        unit_texts=[u.text for u in units],
        unit_vectors=vectors,
        embedder=resources.embedder,
        semantic_threshold=sc.skill_semantic_threshold,
    )
    resume_vec = mean_vector(vectors) if len(units) else None
    overall_raw = (
        float(resume_vec @ jv.jd_mean) if (resume_vec is not None and jv.jd_mean is not None) else 0.0
    )
    overall = calibrate(overall_raw, sc.overall_floor, sc.overall_ceiling)

    weights = {
        "requirement_coverage": sc.weight_requirements,
        "skill_match": sc.weight_skills,
        "overall_similarity": sc.weight_overall,
    }
    if not skills.applicable:
        weights.pop("skill_match")
    if not job.requirements:
        weights.pop("requirement_coverage")
    total_w = sum(weights.values()) or 1.0
    weights = {k: round(v / total_w, 4) for k, v in weights.items()}
    components = {
        "requirement_coverage": req_score,
        "skill_match": skills.score,
        "overall_similarity": overall,
    }
    score = sum(components[k] * w for k, w in weights.items()) if units else 0.0
    return ScoreResult(
        score=round(score, 2),
        requirement_coverage=req_score,
        skill_match=skills.score,
        overall_similarity=overall,
        overall_raw=round(overall_raw, 4),
        requirements=matches,
        skills=skills,
        weights=weights,
        units=units,
        resume_vector=resume_vec,
    )


def compute_scores(
    all_units: list[TextUnit], job: ParsedJob, jv: JobVectors, resources: Resources, settings: Settings
) -> ScoringOutcome:
    min_words = settings.scoring.min_unit_words
    clean_units = select_units(all_units, "clean", min_words)
    raw_units = select_units(all_units, "raw", min_words)
    texts = {u.index: sbert_text(u, settings.sbert_input) for u in raw_units}
    ordered = list(texts)
    encoded = resources.embedder.encode([texts[i] for i in ordered])
    by_index = {idx: encoded[k] for k, idx in enumerate(ordered)}
    dim = resources.embedder.dim

    def vecs(units: list[TextUnit]) -> np.ndarray:
        return np.stack([by_index[u.index] for u in units]) if units else np.zeros((0, dim), dtype=np.float32)

    clean = score_units(clean_units, vecs(clean_units), job, jv, resources, settings)
    raw = score_units(raw_units, vecs(raw_units), job, jv, resources, settings)
    delta = round(max(0.0, raw.score - clean.score), 2)
    flags: list[Flag] = []
    if delta >= settings.scoring.gaming_delta_threshold:
        flags.append(_inflation_flag(clean, raw, delta, settings.scoring.gaming_delta_threshold))
    counts = {
        "units_total": len(all_units),
        "units_scored": len(clean_units),
        "units_raw": len(raw_units),
        "excluded_hidden": sum(1 for u in all_units if not u.visible and u.source not in NON_PAGE_SOURCES),
        "excluded_jd_copy": sum(1 for u in all_units if u.visible and u.is_jd_copy),
        "excluded_personal": sum(1 for u in all_units if u.excluded == "personal_details"),
        "excluded_contact": sum(1 for u in all_units if u.excluded == "contact"),
    }
    return ScoringOutcome(
        clean=clean, raw=raw, gaming_delta=delta, flags=flags, unit_vectors=by_index, counts=counts
    )


def _inflation_flag(clean: ScoreResult, raw: ScoreResult, delta: float, threshold: float) -> Flag:
    """Explain the gap: which hidden / copied units became the best match for a requirement."""
    by_index = {u.index: u for u in raw.units}
    contributors: list[TextUnit] = []
    for c, r in zip(clean.requirements, raw.requirements, strict=True):
        unit = by_index.get(r.unit_index) if r.unit_index is not None else None
        if (
            unit is not None
            and (not unit.visible or unit.is_jd_copy)
            and r.similarity > c.similarity + 0.02
            and unit not in contributors
        ):
            contributors.append(unit)
    first = contributors[0] if contributors else None
    regions: list[dict[str, Any]] = [{"page": p, "bbox": list(b)} for u in contributors for p, b in u.regions]
    return Flag(
        code="SCORE_INFLATION_ATTEMPT",
        severity=Severity.HIGH,
        detector="scoring",
        message=f"Hidden/copied content raises the score by {delta:.1f} points ({clean.score:.1f} → {raw.score:.1f})",
        evidence=Evidence(
            page=first.page if first else None,
            bbox=first.bbox if first else None,
            snippet=snippet(" | ".join(u.text for u in contributors[:3])) if contributors else None,
            measured_value=delta,
            threshold=threshold,
            location="hidden" if first and not first.visible else ("visible" if first else None),
            regions=regions[:20],
            details={
                "clean_score": clean.score,
                "raw_score": raw.score,
                "clean_components": {
                    "requirement_coverage": clean.requirement_coverage,
                    "skill_match": clean.skill_match,
                    "overall_similarity": clean.overall_similarity,
                },
                "raw_components": {
                    "requirement_coverage": raw.requirement_coverage,
                    "skill_match": raw.skill_match,
                    "overall_similarity": raw.overall_similarity,
                },
                "skills_only_in_hidden_or_copied": sorted(
                    set(raw.skills.matched) - set(clean.skills.matched)
                ),
            },
        ),
    )
