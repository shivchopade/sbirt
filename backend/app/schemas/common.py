from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.enums import HumanDecision, ResumeStatus, Severity, SystemDecision

__all__ = [
    "ErrorDetail",
    "ErrorResponse",
    "HealthResponse",
    "HumanDecision",
    "ResumeStatus",
    "Severity",
    "SystemDecision",
]


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    models_loaded: bool
    sbert_model: str
    embedding_dim: int
    device: str | None
    version: str
