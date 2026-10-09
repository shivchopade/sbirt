from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import numpy as np

    from app.core.config import Settings
    from app.services.resources import Resources


@dataclass
class Requirement:
    index: int
    text: str
    weight: float
    section: str  # heading the requirement was found under
    kind: str  # must | nice | responsibility | general
    parts: list[str] = field(default_factory=list)  # clauses of a compound requirement ("X and Y")


@dataclass
class SkillRequirement:
    name: str  # canonical skill id
    display: str
    weight: float


@dataclass
class ParsedJob:
    title: str
    jd_text: str
    sentences: list[str]  # every JD sentence (incl. boilerplate) — used for copy detection
    requirements: list[Requirement]
    skills: list[SkillRequirement]
    keywords: set[str]  # canonical skills + salient content lemmas
    content_sentences: list[str] = field(default_factory=list)  # non-boilerplate text for pooled similarity

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "sentences": self.sentences,
            "requirements": [r.__dict__ for r in self.requirements],
            "skills": [s.__dict__ for s in self.skills],
            "keywords": sorted(self.keywords),
            "content_sentences": self.content_sentences,
        }

    @classmethod
    def from_dict(cls, jd_text: str, data: dict[str, Any]) -> ParsedJob:
        return cls(
            title=data.get("title", ""),
            jd_text=jd_text,
            sentences=list(data.get("sentences", [])),
            requirements=[Requirement(**r) for r in data.get("requirements", [])],
            skills=[SkillRequirement(**s) for s in data.get("skills", [])],
            keywords=set(data.get("keywords", [])),
            content_sentences=list(data.get("content_sentences", [])),
        )


class DuplicateIndex(Protocol):
    """Lookups the duplicate detector needs; backed by Postgres + pgvector in production."""

    def same_sha256(self, sha256: str) -> list[dict[str, Any]]: ...

    def nearest_resumes(self, embedding: np.ndarray, limit: int = 3) -> list[dict[str, Any]]: ...

    def same_contact(self, email: str | None, phone: str | None) -> list[dict[str, Any]]: ...


@dataclass
class JobContext:
    """Everything a detector may need besides the document itself."""

    parsed: ParsedJob
    settings: Settings
    resources: Resources
    job_id: str | None = None
    resume_id: str | None = None
    duplicate_index: DuplicateIndex | None = None
    resume_embedding: np.ndarray | None = None
    cache: dict[str, Any] = field(default_factory=dict)
