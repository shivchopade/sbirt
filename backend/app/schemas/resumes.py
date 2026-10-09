from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.enums import HumanDecision, ResumeStatus, Severity, SystemDecision


class UploadedResume(BaseModel):
    resume_id: UUID
    file_name: str
    status: ResumeStatus
    error: str | None = None


class UploadResponse(BaseModel):
    job_id: UUID
    accepted: int
    rejected: int
    resumes: list[UploadedResume]


class CandidateRow(BaseModel):
    resume_id: UUID
    file_name: str
    candidate_name: str | None
    email: str | None
    status: ResumeStatus
    status_reason: str | None
    clean_score: float | None
    raw_score: float | None
    gaming_delta: float | None
    risk_score: int | None
    risk_level: Severity | None
    flag_count: int
    flag_codes: list[str]
    system_decision: SystemDecision | None
    human_decision: HumanDecision | None
    final_decision: str | None
    created_at: datetime
    processed_at: datetime | None


class CandidateList(BaseModel):
    job_id: UUID
    total: int
    processing: int = Field(description="PENDING + PROCESSING rows — the UI polls while this is > 0")
    items: list[CandidateRow]


class DecisionUpdate(BaseModel):
    decision: HumanDecision
    note: str | None = Field(default=None, max_length=2000)


class DecisionHistoryItem(BaseModel):
    actor: Literal["system", "human"]
    system_decision: SystemDecision | None
    human_decision: HumanDecision | None
    reviewer_note: str | None
    reasons: list[str]
    decided_at: datetime


class DecisionOut(BaseModel):
    system: SystemDecision | None
    human: HumanDecision | None
    final: str | None = Field(description="Human decision when present, otherwise the system decision")
    reviewer_note: str | None
    reasons: list[str]
    decided_at: datetime | None
    history: list[DecisionHistoryItem]


class ShortlistEntry(BaseModel):
    resume_id: UUID
    candidate_name: str | None
    email: str | None
    phone: str | None
    clean_score: float | None
    source: Literal["system", "human"]
    decided_at: datetime | None


class ShortlistResponse(BaseModel):
    job_id: UUID
    job_title: str
    items: list[ShortlistEntry]


class RescoreResponse(BaseModel):
    resume_id: UUID
    status: ResumeStatus
