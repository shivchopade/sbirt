"""Requirement coverage: for each JD requirement, the best-matching resume unit (max cosine)."""

from __future__ import annotations

from dataclasses import dataclass, field

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
    method: str = "whole"  # whole | clauses
    parts: list[dict] = field(default_factory=list)  # per clause: text, best_resume_line, similarity


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
    part_vectors: list[np.ndarray | None] | None = None,
) -> tuple[float, list[RequirementMatch]]:
    """Best match per requirement. A compound requirement ("X and Y") scores max(whole, mean(clauses)), so two
    different resume lines may cover its two halves — decomposition can only help, never hurt."""
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
        match = RequirementMatch(
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
        pv = part_vectors[i] if part_vectors else None
        if pv is not None and len(pv) and len(req.parts) == len(pv):
            psims = pv @ unit_vectors.T
            idx = [int(np.argmax(row)) for row in psims]
            values = [float(psims[k, idx[k]]) for k in range(len(idx))]
            match.parts = [
                {
                    "text": req.parts[k],
                    "best_resume_line": units[idx[k]].text,
                    "similarity": round(values[k], 4),
                }
                for k in range(len(idx))
            ]
            mean = float(np.mean(values))
            if mean > best:
                lines = list(dict.fromkeys(units[k].text for k in idx))
                match.method = "clauses"
                match.similarity = round(mean, 4)
                match.calibrated = calibrate(mean, floor, ceiling)
                match.met = mean >= met_threshold
                match.best_resume_line = " | ".join(lines)
                match.resume_section = units[idx[0]].section
                match.unit_index = units[idx[int(np.argmax(values))]].index
        matches.append(match)
    total = sum(m.weight for m in matches)
    score = sum(m.weight * m.calibrated for m in matches) / total if total else 0.0
    return round(score, 2), matches
