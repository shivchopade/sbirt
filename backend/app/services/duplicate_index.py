"""DuplicateIndex backed by PostgreSQL + pgvector (HNSW cosine index on resumes.embedding)."""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Resume


class PgDuplicateIndex:
    def __init__(self, session: Session, job_id: uuid.UUID, resume_id: uuid.UUID, scope: str = "job"):
        self.session = session
        self.job_id = job_id
        self.resume_id = resume_id
        self.scope = scope

    def _base(self):
        conditions = [Resume.id != self.resume_id]
        if self.scope == "job":
            conditions.append(Resume.job_id == self.job_id)
        return and_(*conditions)

    def same_sha256(self, sha256: str) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(Resume.id, Resume.file_name).where(self._base(), Resume.sha256 == sha256)
        ).all()
        return [{"resume_id": str(r.id), "file_name": r.file_name} for r in rows]

    def nearest_resumes(self, embedding: np.ndarray, limit: int = 3) -> list[dict[str, Any]]:
        distance = Resume.embedding.cosine_distance(embedding.tolist())
        rows = self.session.execute(
            select(Resume.id, Resume.file_name, Resume.candidate_name, distance.label("d"))
            .where(self._base(), Resume.embedding.is_not(None))
            .order_by(distance)
            .limit(limit)
        ).all()
        return [
            {
                "resume_id": str(r.id),
                "file_name": r.file_name,
                "candidate_name": r.candidate_name,
                "similarity": round(1 - float(r.d), 4),
            }
            for r in rows
        ]

    def same_contact(self, email: str | None, phone: str | None) -> list[dict[str, Any]]:
        matches = []
        if email:
            matches.append(func.lower(Resume.email) == email.lower())
        if phone:
            matches.append(Resume.phone == phone)
        if not matches:
            return []
        rows = self.session.execute(
            select(Resume.id, Resume.candidate_name, Resume.email, Resume.phone).where(
                self._base(), or_(*matches)
            )
        ).all()
        return [
            {"resume_id": str(r.id), "candidate_name": r.candidate_name, "email": r.email, "phone": r.phone}
            for r in rows
        ]
