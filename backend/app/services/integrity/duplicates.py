from __future__ import annotations

from app.domain.document import ParsedDocument
from app.domain.enums import Severity
from app.domain.flags import Evidence, Flag
from app.domain.job import JobContext
from app.services.integrity.base import register


def _norm_name(name: str | None) -> str:
    return " ".join((name or "").lower().split())


@register
class DuplicateDetector:
    """Catches the same applicant (or the same template) entering a job more than once: byte-identical files
    (same SHA-256), resumes whose overall embedding is almost identical to another candidate's (pgvector cosine
    ≥ 0.97 — copied or template-farmed resumes) and the same email/phone under a different name. Needs the
    database; skipped when no duplicate index is available (e.g. offline scripts)."""

    name = "duplicates"

    def detect(self, doc: ParsedDocument, ctx: JobContext) -> list[Flag]:
        index = ctx.duplicate_index
        if index is None:
            return []
        integ = ctx.settings.integrity
        flags: list[Flag] = []

        same_file = index.same_sha256(doc.sha256)
        if same_file:
            flags.append(
                Flag(
                    code="DUPLICATE_FILE",
                    severity=Severity.MEDIUM,
                    detector=self.name,
                    message=f"Exactly the same file was already uploaded ({same_file[0]['file_name']})",
                    evidence=Evidence(
                        snippet=doc.sha256[:16] + "…",
                        measured_value=float(len(same_file)),
                        threshold=1.0,
                        location="file",
                        details={"duplicates": same_file[:5]},
                    ),
                )
            )

        same_ids = {d["resume_id"] for d in same_file}
        if ctx.resume_embedding is not None:
            near = [
                n
                for n in index.nearest_resumes(ctx.resume_embedding, limit=5)
                if n["resume_id"] not in same_ids and n["similarity"] >= integ.near_duplicate_cosine
            ]
            if near:
                flags.append(
                    Flag(
                        code="NEAR_DUPLICATE_RESUME",
                        severity=Severity.MEDIUM,
                        detector=self.name,
                        message=f"Resume content is {near[0]['similarity']:.1%} similar to {near[0].get('candidate_name') or near[0]['file_name']}",
                        evidence=Evidence(
                            measured_value=round(near[0]["similarity"], 4),
                            threshold=integ.near_duplicate_cosine,
                            location="file",
                            details={"similar_resumes": near},
                        ),
                    )
                )

        email, phone = doc.contact.email, doc.contact.phone
        if email or phone:
            mine = _norm_name(doc.contact.name)
            conflicts = [
                c
                for c in index.same_contact(email, phone)
                if mine and _norm_name(c.get("candidate_name")) not in ("", mine)
            ]
            if conflicts:
                flags.append(
                    Flag(
                        code="IDENTITY_CONFLICT",
                        severity=Severity.HIGH,
                        detector=self.name,
                        message=f"Same email/phone as {conflicts[0]['candidate_name']} but a different name ({doc.contact.name})",
                        evidence=Evidence(
                            snippet=" / ".join(v for v in (email, phone) if v),
                            measured_value=float(len(conflicts)),
                            threshold=1.0,
                            location="visible",
                            details={"conflicts": conflicts[:5]},
                        ),
                    )
                )
        return flags
