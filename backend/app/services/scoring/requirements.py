"""Requirement coverage: for each JD requirement, the best-matching resume unit (max cosine)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.domain.document import TextUnit
from app.domain.job import Requirement


@dataclass
class RequirementMatch:
    jd_requirement: str
    best_resume_line: str | None
    resume_section: str | None
    similarity: float
    calibrated: float
    met: bool
    weight: float
    kind: str
    unit_index: int | None = None


def calibrate(cosine: float, floor: float, ceiling: float) -> float:
    """Map a raw cosine to 0–100 with a configurable floor/ceiling (defaults 0.25 → 0, 0.75 → 100)."""
    if ceiling <= floor:
        return 0.0
    return round(100.0 * min(1.0, max(0.0, (cosine - floor) / (ceiling - floor))), 2)


def requirement_coverage(
    requirements: list[Requirement],
    req_vectors: np.ndarray,
    units: list[TextUnit],
    unit_vectors: np.ndarray,
    *,
    floor: float,
    ceiling: float,
    met_threshold: float,
) -> tuple[float, list[RequirementMatch]]:
    if not requirements:
        return 0.0, []
    matches: list[RequirementMatch] = []
    sims = req_vectors @ unit_vectors.T if len(units) else np.zeros((len(requirements), 0))
    for i, req in enumerate(requirements):
        if sims.shape[1] == 0:
            matches.append(RequirementMatch(req.text, None, None, 0.0, 0.0, False, req.weight, req.kind))
            continue
        j = int(np.argmax(sims[i]))
        best = float(sims[i, j])
        matches.append(
            RequirementMatch(
                jd_requirement=req.text,
                best_resume_line=units[j].text,
                resume_section=units[j].section,
                similarity=round(best, 4),
                calibrated=calibrate(best, floor, ceiling),
                met=best >= met_threshold,
                weight=req.weight,
                kind=req.kind,
                unit_index=units[j].index,
            )
        )
    total = sum(m.weight for m in matches)
    score = sum(m.weight * m.calibrated for m in matches) / total if total else 0.0
    return round(score, 2), matches
