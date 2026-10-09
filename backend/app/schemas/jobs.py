from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class JobCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300, examples=["Backend Engineer (Python)"])
    jd_text: str = Field(min_length=30, max_length=50_000, description="Full job description text")


class RequirementOut(BaseModel):
    index: int
    text: str
    weight: float = Field(description="1.0 = must have / responsibility, 0.5 = nice to have")
    section: str
    kind: str
    parts: list[str] = Field(default_factory=list, description="Clauses of a compound requirement")


class SkillOut(BaseModel):
    name: str = Field(description="Canonical skill id from skills.json")
    display: str
    weight: float


class JobSummary(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    requirement_count: int
    skill_count: int
    resume_count: int
    processing_count: int


class JobDetail(JobSummary):
    jd_text: str
    requirements: list[RequirementOut]
    required_skills: list[SkillOut]
    keywords: list[str]
