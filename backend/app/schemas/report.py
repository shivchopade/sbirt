from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.enums import ResumeStatus, Severity
from app.schemas.resumes import DecisionOut


class CandidateInfo(BaseModel):
    name: str | None
    email: str | None
    phone: str | None
    links: list[str] = Field(default_factory=list)


class RequirementPartOut(BaseModel):
    text: str
    best_resume_line: str | None
    similarity: float


class RequirementMatchOut(BaseModel):
    jd_requirement: str
    best_resume_line: str | None
    resume_section: str | None
    similarity: float = Field(description="Raw cosine similarity")
    calibrated: float = Field(description="Cosine mapped to 0–100")
    met: bool
    weight: float
    kind: str
    method: str = Field(
        default="whole", description="whole, or clauses when a compound requirement was split"
    )
    parts: list[RequirementPartOut] = Field(default_factory=list)


class SkillDetailOut(BaseModel):
    skill: str
    display: str
    weight: float
    matched: bool
    method: str | None = Field(description="lexical (PhraseMatcher on lemmatized text) or semantic (SBERT)")
    evidence: str | None
    similarity: float | None


class SkillsOut(BaseModel):
    matched: list[str]
    missing: list[str]
    details: list[SkillDetailOut]


class ComponentScores(BaseModel):
    requirement_coverage: float
    skill_match: float
    overall_similarity: float


class ScoreBreakdown(BaseModel):
    requirement_coverage: float
    skill_match: float
    overall_similarity: float
    overall_similarity_raw: float
    weights: dict[str, float]
    requirements: list[RequirementMatchOut]
    skills: SkillsOut
    raw_components: ComponentScores
    units: dict[str, int]
    sbert_model: str
    sbert_input: str
    timings_ms: dict[str, float]


class ScoresOut(BaseModel):
    clean_score: float = Field(description="Official score: visible text minus verbatim JD-copied lines")
    raw_score: float = Field(description="Score over everything extracted (what a naive ATS would compute)")
    gaming_delta: float
    breakdown: ScoreBreakdown


class RegionOut(BaseModel):
    page: int
    bbox: list[float]


class EvidenceOut(BaseModel):
    page: int | None = None
    bbox: list[float] | None = None
    snippet: str | None = None
    measured_value: float | None = None
    threshold: float | None = None
    location: str | None = None
    regions: list[RegionOut] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class FlagOut(BaseModel):
    code: str
    severity: Severity
    detector: str
    message: str
    evidence: EvidenceOut


class IntegrityOut(BaseModel):
    risk_score: int
    risk_level: Severity | None
    flags: list[FlagOut]


class PreviewPage(BaseModel):
    page: int
    url: str


class TextStats(BaseModel):
    visible_chars: int
    hidden_chars: int
    units: int
    hidden_units: int
    jd_copy_units: int


class ResumeReport(BaseModel):
    resume_id: UUID
    job_id: UUID
    job_title: str
    file_name: str
    file_type: str | None
    status: ResumeStatus
    status_reason: str | None
    page_count: int | None
    candidate: CandidateInfo
    decision: DecisionOut
    scores: ScoresOut | None
    integrity: IntegrityOut
    previews: list[PreviewPage]
    text: TextStats
    hidden_text_excerpt: str | None
    created_at: datetime
    processed_at: datetime | None
