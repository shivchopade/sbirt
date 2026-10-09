"""System decision. Flags never auto-reject: anything suspicious goes to a human."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import DecisionSettings
from app.domain.enums import ResumeStatus, Severity, SystemDecision
from app.domain.flags import Flag


@dataclass
class DecisionResult:
    system: SystemDecision
    reasons: list[str] = field(default_factory=list)


def decide(
    status: ResumeStatus, clean_score: float | None, flags: list[Flag], ds: DecisionSettings
) -> DecisionResult:
    critical = sorted({f.code for f in flags if Severity(f.severity) == Severity.CRITICAL})
    high = sorted({f.code for f in flags if Severity(f.severity) == Severity.HIGH})
    if critical:
        return DecisionResult(
            SystemDecision.FLAGGED,
            [f"critical integrity flag(s): {', '.join(critical)} — a human must review"],
        )
    if status in (ResumeStatus.UNPARSEABLE, ResumeStatus.FAILED):
        return DecisionResult(
            SystemDecision.NEEDS_REVIEW,
            [f"document is {status.value.lower()} — the content could not be scored automatically"],
        )
    score = clean_score or 0.0
    if score >= ds.shortlist_score and not high:
        return DecisionResult(
            SystemDecision.SHORTLISTED,
            [f"clean score {score:.1f} ≥ {ds.shortlist_score:g} with no high-severity flags"],
        )
    if score >= ds.shortlist_score:
        return DecisionResult(
            SystemDecision.NEEDS_REVIEW,
            [
                f"clean score {score:.1f} ≥ {ds.shortlist_score:g} but high-severity flag(s): {', '.join(high)}"
            ],
        )
    if score >= ds.review_score:
        return DecisionResult(
            SystemDecision.NEEDS_REVIEW,
            [f"borderline clean score {score:.1f} (review band {ds.review_score:g}–{ds.shortlist_score:g})"],
        )
    reasons = [f"clean score {score:.1f} < {ds.review_score:g}"]
    if high:
        reasons.append(f"flags shown for context: {', '.join(high)}")
    return DecisionResult(SystemDecision.NOT_SHORTLISTED, reasons)
