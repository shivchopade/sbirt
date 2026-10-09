"""initial schema: jobs, resumes, units, scores, flags, decisions, events (+ pgvector HNSW indexes)

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-09

The vector dimension is read from settings (EMBEDDING_DIM) when the migration runs.
Switching to a model with a different dimension = new migration + `scripts/reembed.py`.
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

DIM = get_settings().embedding_dim


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("jd_text", sa.Text(), nullable=False),
        sa.Column("requirements", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("required_skills", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("jd_analysis", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("jd_embedding", Vector(DIM), nullable=True),
        sa.Column("embedding_model", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "resumes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("mime", sa.String(120), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("storage_path", sa.String(1000), nullable=True),
        sa.Column("quarantined", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("candidate_name", sa.String(300), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("visible_text", sa.Text(), nullable=True),
        sa.Column("hidden_text", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(DIM), nullable=True),
        sa.Column("embedding_model", sa.String(200), nullable=True),
        sa.Column("analysis", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_resumes_job_id", "resumes", ["job_id"])
    op.create_index("ix_resumes_sha256", "resumes", ["sha256"])
    op.create_index("ix_resumes_status", "resumes", ["status"])
    op.create_index("ix_resumes_email", "resumes", ["email"])
    op.create_index("ix_resumes_phone", "resumes", ["phone"])

    op.create_table(
        "resume_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resume_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("bbox", postgresql.JSONB(), nullable=True),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_jd_copy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("excluded", sa.String(40), nullable=True),
        sa.Column("embedding", Vector(DIM), nullable=True),
    )
    op.create_index("ix_resume_units_resume_id", "resume_units", ["resume_id"])

    op.create_table(
        "scores",
        sa.Column("resume_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resumes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("clean_score", sa.Float(), nullable=False),
        sa.Column("raw_score", sa.Float(), nullable=False),
        sa.Column("gaming_delta", sa.Float(), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risk_level", sa.String(10), nullable=True),
        sa.Column("breakdown", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "integrity_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resume_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("detector", sa.String(60), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_integrity_flags_resume_id", "integrity_flags", ["resume_id"])
    op.create_index("ix_integrity_flags_code", "integrity_flags", ["code"])

    op.create_table(
        "decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resume_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("system_decision", sa.String(20), nullable=True),
        sa.Column("human_decision", sa.String(30), nullable=True),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column("reasons", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("actor", sa.String(20), nullable=False, server_default="system"),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_decisions_resume_id", "decisions", ["resume_id"])

    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=True, unique=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_events_type", "events", ["type"])

    op.execute(
        "CREATE INDEX ix_resume_units_embedding_hnsw ON resume_units "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX ix_resumes_embedding_hnsw ON resumes "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    for table in ("events", "decisions", "integrity_flags", "scores", "resume_units", "resumes", "jobs"):
        op.drop_table(table)
