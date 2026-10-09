"""Integrity risk score: sum of severity weights (critical 40, high 25, medium 10, low 3), capped at 100."""

from __future__ import annotations

from app.core.config import RiskSettings
from app.domain.enums import Severity
from app.domain.flags import Flag


def risk_score(flags: list[Flag], risk: RiskSettings) -> tuple[int, Severity | None]:
    weights = {
        Severity.CRITICAL: risk.critical,
        Severity.HIGH: risk.high,
        Severity.MEDIUM: risk.medium,
        Severity.LOW: risk.low,
    }
    score = min(risk.cap, sum(weights[Severity(f.severity)] for f in flags))
    level = max((Severity(f.severity) for f in flags), key=lambda s: s.rank, default=None)
    return score, level
