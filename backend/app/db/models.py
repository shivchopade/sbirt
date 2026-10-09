"""SQLAlchemy models. Vector columns use the configured embedding dimension (MiniLM = 384, mpnet = 768)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.config import get_settings

EMBED_DIM = get_settings().embedding_dim


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(300))
    jd_text: Mapped[str] = mapped_column(Text)
    requirements: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    required_skills: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    jd_analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    jd_embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    resumes: Mapped[list[Resume]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    file_name: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    mime: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    storage_path: Mapped[str | None] = mapped_column(String(1000))
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)
    candidate_name: Mapped[str | None] = mapped_column(String(300))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(40), index=True)
    page_count: Mapped[int | None] = mapped_column(Integer)
    visible_text: Mapped[str | None] = mapped_column(Text)
    hidden_text: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    analysis: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict
    )  # preview regions, unicode stats, ...
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship(back_populates="resumes")
    units: Mapped[list[ResumeUnit]] = relationship(
        cascade="all, delete-orphan", order_by="ResumeUnit.position"
    )
    score: Mapped[Score | None] = relationship(cascade="all, delete-orphan", uselist=False)
    flags: Mapped[list[IntegrityFlag]] = relationship(cascade="all, delete-orphan")
    decisions: Mapped[list[Decision]] = relationship(
        cascade="all, delete-orphan", order_by="Decision.decided_at"
    )


class ResumeUnit(Base):
    __tablename__ = "resume_units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list[float] | None] = mapped_column(JSONB)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    is_jd_copy: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded: Mapped[str | None] = mapped_column(String(40))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)


class Score(Base):
    __tablename__ = "scores"

    resume_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resumes.id", ondelete="CASCADE"), primary_key=True
    )
    clean_score: Mapped[float] = mapped_column(Float)
    raw_score: Mapped[float] = mapped_column(Float)
    gaming_delta: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_level: Mapped[str | None] = mapped_column(String(10))
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IntegrityFlag(Base):
    __tablename__ = "integrity_flags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(60), index=True)
    severity: Mapped[str] = mapped_column(String(10))
    detector: Mapped[str] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Decision(Base):
    """Append-only audit trail. The latest row is the current decision."""

    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"), index=True)
    system_decision: Mapped[str | None] = mapped_column(String(20))
    human_decision: Mapped[str | None] = mapped_column(String(30))
    reviewer_note: Mapped[str | None] = mapped_column(Text)
    reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    actor: Mapped[str] = mapped_column(String(20), default="system")  # system | human
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    """Transactional outbox consumed by future agents (e.g. interview scheduling)."""

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(80), index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index(
    "ix_resume_units_embedding_hnsw",
    ResumeUnit.embedding,
    postgresql_using="hnsw",
    postgresql_with={"m": 16, "ef_construction": 64},
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
Index(
    "ix_resumes_embedding_hnsw",
    Resume.embedding,
    postgresql_using="hnsw",
    postgresql_with={"m": 16, "ef_construction": 64},
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
