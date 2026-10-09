"""Resumes: upload → background processing → persisted report; candidate table, human decisions, shortlist."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AppError, NotFoundError
from app.core.logging import resume_id_var
from app.db.models import Decision, Event, IntegrityFlag, Job, Resume, ResumeUnit, Score
from app.db.session import session_scope
from app.domain.enums import HumanDecision, ResumeStatus, Severity, SystemDecision
from app.schemas.report import (
    CandidateInfo,
    ComponentScores,
    EvidenceOut,
    FlagOut,
    IntegrityOut,
    PreviewPage,
    RegionOut,
    RequirementMatchOut,
    ResumeReport,
    ScoreBreakdown,
    ScoresOut,
    SkillDetailOut,
    SkillsOut,
    TextStats,
)
from app.schemas.resumes import (
    CandidateList,
    CandidateRow,
    DecisionHistoryItem,
    DecisionOut,
    DecisionUpdate,
    RescoreResponse,
    ShortlistEntry,
    ShortlistResponse,
    UploadedResume,
    UploadResponse,
)
from app.services.duplicate_index import PgDuplicateIndex
from app.services.job_service import get_job_row, job_analysis
from app.services.pipeline import AnalysisResult, analyze_document
from app.services.resources import Resources
from app.services.storage import Storage
from app.services.task_queue import TaskQueue
from app.services.validation import validate_upload

log = logging.getLogger(__name__)
_RANK = {s.value: s.rank for s in Severity}


# ------------------------------------------------------------------------------------------ upload


def upload_resumes(
    session: Session,
    job_id: uuid.UUID,
    files: list[tuple[str, bytes]],
    queue: TaskQueue,
    resources: Resources,
) -> UploadResponse:
    settings = resources.settings
    get_job_row(session, job_id)
    if not files:
        raise AppError("NO_FILES", "Attach at least one PDF or DOCX file", 422)
    if len(files) > settings.upload.max_files_per_request:
        raise AppError(
            "TOO_MANY_FILES", f"At most {settings.upload.max_files_per_request} files per upload", 413
        )
    storage = Storage(settings)
    rows: list[Resume] = []
    for raw_name, data in files:
        name = Path(raw_name or "resume").name[:200] or "resume"
        check = validate_upload(data, name, settings)
        resume = Resume(
            job_id=job_id, file_name=name, sha256=check.sha256, size_bytes=len(data), mime=check.mime
        )
        if check.ok:
            resume.storage_path = str(storage.save_upload(data, check.kind or "bin"))
            resume.status = ResumeStatus.PENDING.value
        else:  # one bad file never breaks the batch: it gets a FAILED row with the reason
            resume.status = ResumeStatus.FAILED.value
            resume.status_reason = check.error
            resume.processed_at = datetime.now(UTC)
        session.add(resume)
        rows.append(resume)
    session.commit()
    for resume in rows:
        if resume.status == ResumeStatus.PENDING.value:
            queue.enqueue(process_resume, str(resume.id), resources)
    return UploadResponse(
        job_id=job_id,
        accepted=sum(r.status == ResumeStatus.PENDING.value for r in rows),
        rejected=sum(r.status == ResumeStatus.FAILED.value for r in rows),
        resumes=[
            UploadedResume(
                resume_id=r.id, file_name=r.file_name, status=ResumeStatus(r.status), error=r.status_reason
            )
            for r in rows
        ],
    )


def pending_resume_ids() -> list[str]:
    with session_scope() as session:
        rows = session.scalars(
            select(Resume.id).where(
                Resume.status.in_([ResumeStatus.PENDING.value, ResumeStatus.PROCESSING.value])
            )
        ).all()
        return [str(r) for r in rows]


# ------------------------------------------------------------------------------------------ processing


def process_resume(resume_id: str, resources: Resources) -> None:
    """Background task. Catches everything: a broken file ends as FAILED with a reason, never as a crash."""
    token = resume_id_var.set(resume_id)
    settings = resources.settings
    storage = Storage(settings)
    rid = uuid.UUID(resume_id)
    try:
        with session_scope() as session:
            resume = session.get(Resume, rid)
            if resume is None or not resume.storage_path:
                return
            resume.status = ResumeStatus.PROCESSING.value
            path, file_name, job_id = resume.storage_path, resume.file_name, resume.job_id
        data = storage.read(path)
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            analysis = job_analysis(job, resources)
            index = PgDuplicateIndex(session, job_id, rid, settings.integrity.duplicate_scope)
            result = analyze_document(
                data,
                file_name,
                analysis,
                resources,
                duplicate_index=index,
                job_id=str(job_id),
                resume_id=resume_id,
            )
            resume = session.get(Resume, rid)
            if resume is not None:
                _persist(session, resume, result, resources, storage)
        log.info(
            "resume processed",
            extra={
                "status": result.status.value,
                "decision": result.decision.system.value,
                "ms": result.timings_ms.get("total"),
            },
        )
    except Exception as exc:
        log.exception("processing failed")
        with session_scope() as session:
            resume = session.get(Resume, rid)
            if resume is not None:
                resume.status = ResumeStatus.FAILED.value
                resume.status_reason = f"Processing failed ({exc.__class__.__name__})"
                resume.processed_at = datetime.now(UTC)
    finally:
        resume_id_var.reset(token)


def _persist(
    session: Session, resume: Resume, result: AnalysisResult, resources: Resources, storage: Storage
) -> None:
    settings = resources.settings
    doc = result.doc
    resume.status = result.status.value
    resume.status_reason = result.status_reason
    resume.candidate_name = result.contact.name
    resume.email = result.contact.email
    resume.phone = result.contact.phone
    resume.page_count = result.page_count
    resume.visible_text = doc.visible_text if doc else None
    resume.hidden_text = doc.hidden_text if doc else None
    vector = result.scores.clean.resume_vector if result.scores else None
    resume.embedding = vector.tolist() if vector is not None else None
    resume.embedding_model = settings.sbert_model
    resume.processed_at = datetime.now(UTC)
    resume.analysis = _analysis_json(result)
    if result.quarantine and resume.storage_path:
        resume.storage_path = storage.quarantine(resume.storage_path)
        resume.quarantined = True

    session.execute(delete(ResumeUnit).where(ResumeUnit.resume_id == resume.id))
    session.execute(delete(IntegrityFlag).where(IntegrityFlag.resume_id == resume.id))
    if doc is not None and result.scores is not None:
        vectors = result.scores.unit_vectors
        for unit in doc.units:
            vec = vectors.get(unit.index)
            session.add(
                ResumeUnit(
                    resume_id=resume.id,
                    position=unit.index,
                    section=unit.section,
                    text=unit.text,
                    page=unit.page,
                    bbox=list(unit.bbox) if unit.bbox else None,
                    is_visible=unit.visible,
                    is_jd_copy=unit.is_jd_copy,
                    excluded=unit.excluded,
                    embedding=vec.tolist() if vec is not None else None,
                )
            )
    score = session.get(Score, resume.id) or Score(resume_id=resume.id)
    score.clean_score = result.scores.clean.score if result.scores else 0.0
    score.raw_score = result.scores.raw.score if result.scores else 0.0
    score.gaming_delta = result.scores.gaming_delta if result.scores else 0.0
    score.risk_score = result.risk_score
    score.risk_level = result.risk_level.value if result.risk_level else None
    score.breakdown = _breakdown_json(result, resources) if result.scores else {}
    session.add(score)
    for flag in result.flags:
        data = flag.to_dict()
        session.add(
            IntegrityFlag(
                resume_id=resume.id,
                code=data["code"],
                severity=data["severity"],
                detector=data["detector"],
                message=data["message"],
                evidence=data["evidence"],
            )
        )

    latest = session.scalars(
        select(Decision)
        .where(Decision.resume_id == resume.id, Decision.actor == "system")
        .order_by(Decision.decided_at.desc())
        .limit(1)
    ).first()
    if (
        latest is None
        or latest.system_decision != result.decision.system.value
        or latest.reasons != result.decision.reasons
    ):
        session.add(
            Decision(
                resume_id=resume.id,
                system_decision=result.decision.system.value,
                reasons=result.decision.reasons,
                actor="system",
            )
        )
    session.flush()
    if result.decision.system == SystemDecision.SHORTLISTED and _human_decision(session, resume.id) in (
        None,
        HumanDecision.APPROVED_FOR_INTERVIEW.value,
    ):
        _emit_shortlisted(session, resume, "system")
    storage.clear_previews(str(resume.id))


def _analysis_json(result: AnalysisResult) -> dict[str, Any]:
    doc = result.doc
    out: dict[str, Any] = {
        "file_kind": result.file_kind,
        "timings_ms": result.timings_ms,
        "decision_reasons": result.decision.reasons,
        "jd_shingle_fraction": round(result.jd_shingle_fraction, 4),
        "security": {k: v for k, v in result.security.__dict__.items() if k != "details"},
    }
    if doc is None:
        return out
    hidden_boxes: dict[tuple[int, int], dict[str, Any]] = {}
    for span in doc.hidden_spans:
        if span.bbox is None or span.page < 1:
            continue
        key = (span.page, span.line_id)
        box = hidden_boxes.get(key)
        reason = str(span.hidden_reasons[0]) if span.hidden_reasons else "HIDDEN"
        if box is None:
            hidden_boxes[key] = {"page": span.page, "bbox": list(span.bbox), "reason": reason}
        else:
            b = box["bbox"]
            box["bbox"] = [
                min(b[0], span.bbox[0]),
                min(b[1], span.bbox[1]),
                max(b[2], span.bbox[2]),
                max(b[3], span.bbox[3]),
            ]
    copies = [
        {"page": p, "bbox": list(b)} for u in doc.units if u.is_jd_copy and u.visible for p, b in u.regions
    ]
    units = doc.units
    out.update(
        {
            "preview": {"hidden": list(hidden_boxes.values())[:400], "jd_copy": copies[:200]},
            "unicode_stats": doc.unicode_stats,
            "warnings": doc.warnings,
            "links": doc.contact.links,
            "text_stats": {
                "visible_chars": len(doc.visible_text),
                "hidden_chars": len(doc.hidden_text),
                "units": len(units),
                "hidden_units": sum(not u.visible for u in units),
                "jd_copy_units": sum(u.is_jd_copy for u in units),
            },
        }
    )
    return out


def _breakdown_json(result: AnalysisResult, resources: Resources) -> dict[str, Any]:
    assert result.scores is not None
    clean, raw = result.scores.clean, result.scores.raw
    return {
        "requirement_coverage": clean.requirement_coverage,
        "skill_match": clean.skill_match,
        "overall_similarity": clean.overall_similarity,
        "overall_similarity_raw": clean.overall_raw,
        "weights": clean.weights,
        "requirements": [
            {
                "jd_requirement": m.jd_requirement,
                "best_resume_line": m.best_resume_line,
                "resume_section": m.resume_section,
                "similarity": m.similarity,
                "calibrated": m.calibrated,
                "met": m.met,
                "weight": m.weight,
                "kind": m.kind,
            }
            for m in clean.requirements
        ],
        "skills": {
            "matched": clean.skills.matched,
            "missing": clean.skills.missing,
            "details": [d.__dict__ for d in clean.skills.details],
        },
        "raw_components": {
            "requirement_coverage": raw.requirement_coverage,
            "skill_match": raw.skill_match,
            "overall_similarity": raw.overall_similarity,
        },
        "units": result.scores.counts,
        "sbert_model": resources.settings.sbert_model,
        "sbert_input": resources.settings.sbert_input,
        "timings_ms": result.timings_ms,
    }


# ------------------------------------------------------------------------------------------ decisions & events


def _human_decision(session: Session, resume_id: uuid.UUID) -> str | None:
    row = session.scalars(
        select(Decision)
        .where(Decision.resume_id == resume_id, Decision.actor == "human")
        .order_by(Decision.decided_at.desc())
        .limit(1)
    ).first()
    return row.human_decision if row else None


def _emit_shortlisted(session: Session, resume: Resume, source: str) -> None:
    """Transactional outbox: one candidate.shortlisted event per candidate, for the scheduling agent."""
    key = f"candidate.shortlisted:{resume.id}"
    if session.scalar(select(Event.id).where(Event.dedupe_key == key)) is not None:
        return
    score = session.get(Score, resume.id)
    session.add(
        Event(
            type="candidate.shortlisted",
            dedupe_key=key,
            payload={
                "resume_id": str(resume.id),
                "job_id": str(resume.job_id),
                "candidate_name": resume.candidate_name,
                "email": resume.email,
                "phone": resume.phone,
                "clean_score": score.clean_score if score else None,
                "source": source,
            },
        )
    )


def decision_out(decisions: list[Decision]) -> DecisionOut:
    ordered = sorted(decisions, key=lambda d: d.decided_at)
    system = next((d for d in reversed(ordered) if d.actor == "system"), None)
    human = next((d for d in reversed(ordered) if d.actor == "human"), None)
    final = (human.human_decision if human else None) or (system.system_decision if system else None)
    latest = human or system
    return DecisionOut(
        system=SystemDecision(system.system_decision) if system and system.system_decision else None,
        human=HumanDecision(human.human_decision) if human and human.human_decision else None,
        final=final,
        reviewer_note=human.reviewer_note if human else None,
        reasons=list(system.reasons) if system else [],
        decided_at=latest.decided_at if latest else None,
        history=[
            DecisionHistoryItem(
                actor="human" if d.actor == "human" else "system",
                system_decision=SystemDecision(d.system_decision) if d.system_decision else None,
                human_decision=HumanDecision(d.human_decision) if d.human_decision else None,
                reviewer_note=d.reviewer_note,
                reasons=list(d.reasons or []),
                decided_at=d.decided_at,
            )
            for d in ordered
        ],
    )


def set_decision(session: Session, resume_id: uuid.UUID, body: DecisionUpdate) -> DecisionOut:
    resume = get_resume_row(session, resume_id)
    current = decision_out(resume.decisions)
    session.add(
        Decision(
            resume_id=resume.id,
            system_decision=current.system.value if current.system else None,
            human_decision=body.decision.value,
            reviewer_note=(body.note or "").strip() or None,
            actor="human",
            reasons=[],
            decided_at=datetime.now(UTC),
        )
    )
    session.flush()
    if body.decision == HumanDecision.APPROVED_FOR_INTERVIEW:
        _emit_shortlisted(session, resume, "human")
    session.commit()
    session.expire(resume, ["decisions"])
    return decision_out(resume.decisions)


def rescore(
    session: Session, resume_id: uuid.UUID, queue: TaskQueue, resources: Resources
) -> RescoreResponse:
    resume = get_resume_row(session, resume_id)
    if not resume.storage_path:
        raise AppError(
            "NOT_RESCORABLE", "This upload was rejected before processing; upload a valid file instead", 409
        )
    resume.status = ResumeStatus.PENDING.value
    session.commit()
    queue.enqueue(process_resume, str(resume.id), resources)
    return RescoreResponse(resume_id=resume.id, status=ResumeStatus.PENDING)


# ------------------------------------------------------------------------------------------ reads


def get_resume_row(session: Session, resume_id: uuid.UUID) -> Resume:
    resume = session.get(
        Resume, resume_id, options=[selectinload(Resume.decisions), selectinload(Resume.flags)]
    )
    if resume is None:
        raise NotFoundError("Resume", resume_id)
    return resume


def _sorted_flags(flags: list[IntegrityFlag]) -> list[IntegrityFlag]:
    return sorted(
        flags, key=lambda f: (-_RANK.get(f.severity, 0), f.created_at or datetime.min.replace(tzinfo=UTC))
    )


def build_report(session: Session, resume_id: uuid.UUID) -> ResumeReport:
    resume = get_resume_row(session, resume_id)
    job = session.get(Job, resume.job_id)
    score = session.get(Score, resume.id)
    analysis = resume.analysis or {}
    scores = None
    if score is not None and score.breakdown:
        b = score.breakdown
        scores = ScoresOut(
            clean_score=score.clean_score,
            raw_score=score.raw_score,
            gaming_delta=score.gaming_delta,
            breakdown=ScoreBreakdown(
                requirement_coverage=b["requirement_coverage"],
                skill_match=b["skill_match"],
                overall_similarity=b["overall_similarity"],
                overall_similarity_raw=b["overall_similarity_raw"],
                weights=b["weights"],
                requirements=[RequirementMatchOut(**r) for r in b["requirements"]],
                skills=SkillsOut(
                    matched=b["skills"]["matched"],
                    missing=b["skills"]["missing"],
                    details=[SkillDetailOut(**d) for d in b["skills"]["details"]],
                ),
                raw_components=ComponentScores(**b["raw_components"]),
                units=b["units"],
                sbert_model=b["sbert_model"],
                sbert_input=b["sbert_input"],
                timings_ms=b["timings_ms"],
            ),
        )
    flags = [
        FlagOut(
            code=f.code,
            severity=Severity(f.severity),
            detector=f.detector,
            message=f.message,
            evidence=EvidenceOut(
                **{k: v for k, v in (f.evidence or {}).items() if k != "regions"},
                regions=[RegionOut(**r) for r in (f.evidence or {}).get("regions", [])],
            ),
        )
        for f in _sorted_flags(resume.flags)
    ]
    previewable = analysis.get("file_kind") == "pdf" and resume.status in (
        ResumeStatus.DONE.value,
        ResumeStatus.UNPARSEABLE.value,
    )
    stats = analysis.get("text_stats", {})
    return ResumeReport(
        resume_id=resume.id,
        job_id=resume.job_id,
        job_title=job.title if job else "",
        file_name=resume.file_name,
        file_type=analysis.get("file_kind"),
        status=ResumeStatus(resume.status),
        status_reason=resume.status_reason,
        page_count=resume.page_count,
        candidate=CandidateInfo(
            name=resume.candidate_name,
            email=resume.email,
            phone=resume.phone,
            links=analysis.get("links", []),
        ),
        decision=decision_out(resume.decisions),
        scores=scores,
        integrity=IntegrityOut(
            risk_score=score.risk_score if score else 0,
            risk_level=Severity(score.risk_level) if score and score.risk_level else None,
            flags=flags,
        ),
        previews=[
            PreviewPage(page=n, url=f"/api/v1/resumes/{resume.id}/pages/{n}/preview.png")
            for n in range(1, (resume.page_count or 0) + 1)
        ]
        if previewable
        else [],
        text=TextStats(
            visible_chars=stats.get("visible_chars", 0),
            hidden_chars=stats.get("hidden_chars", 0),
            units=stats.get("units", 0),
            hidden_units=stats.get("hidden_units", 0),
            jd_copy_units=stats.get("jd_copy_units", 0),
        ),
        hidden_text_excerpt=(resume.hidden_text or "")[:600] or None,
        created_at=resume.created_at,
        processed_at=resume.processed_at,
    )


def list_candidates(
    session: Session, job_id: uuid.UUID, status: ResumeStatus | None, sort: str
) -> CandidateList:
    get_job_row(session, job_id)
    query = (
        select(Resume)
        .where(Resume.job_id == job_id)
        .options(selectinload(Resume.flags), selectinload(Resume.decisions), selectinload(Resume.score))
    )
    if status is not None:
        query = query.where(Resume.status == status.value)
    resumes = session.scalars(query).all()
    rows: list[CandidateRow] = []
    for r in resumes:
        d = decision_out(r.decisions)
        s = r.score
        rows.append(
            CandidateRow(
                resume_id=r.id,
                file_name=r.file_name,
                candidate_name=r.candidate_name,
                email=r.email,
                status=ResumeStatus(r.status),
                status_reason=r.status_reason,
                clean_score=s.clean_score if s and s.breakdown else None,
                raw_score=s.raw_score if s and s.breakdown else None,
                gaming_delta=s.gaming_delta if s and s.breakdown else None,
                risk_score=s.risk_score if s else None,
                risk_level=Severity(s.risk_level) if s and s.risk_level else None,
                flag_count=len(r.flags),
                flag_codes=[f.code for f in _sorted_flags(r.flags)],
                system_decision=d.system,
                human_decision=d.human,
                final_decision=d.final,
                created_at=r.created_at,
                processed_at=r.processed_at,
            )
        )
    keys = {
        "score": lambda c: (c.clean_score is None, -(c.clean_score or 0)),
        "risk": lambda c: (c.risk_score is None, -(c.risk_score or 0), -(c.clean_score or 0)),
        "name": lambda c: ((c.candidate_name or c.file_name).lower(),),
        "created": lambda c: (c.created_at,),
    }
    rows.sort(key=keys.get(sort, keys["score"]))
    busy = sum(c.status in (ResumeStatus.PENDING, ResumeStatus.PROCESSING) for c in rows)
    return CandidateList(job_id=job_id, total=len(rows), processing=busy, items=rows)


def shortlist(session: Session, job_id: uuid.UUID) -> ShortlistResponse:
    """Read-only: approved (human) or shortlisted (system, not overridden) candidates with contact details."""
    job = get_job_row(session, job_id)
    resumes = session.scalars(
        select(Resume)
        .where(Resume.job_id == job_id)
        .options(selectinload(Resume.decisions), selectinload(Resume.score))
    ).all()
    items = []
    for r in resumes:
        d = decision_out(r.decisions)
        approved = d.human == HumanDecision.APPROVED_FOR_INTERVIEW
        system_ok = d.system == SystemDecision.SHORTLISTED and d.human is None
        if approved or system_ok:
            items.append(
                ShortlistEntry(
                    resume_id=r.id,
                    candidate_name=r.candidate_name,
                    email=r.email,
                    phone=r.phone,
                    clean_score=r.score.clean_score if r.score else None,
                    source="human" if approved else "system",
                    decided_at=d.decided_at,
                )
            )
    items.sort(key=lambda e: -(e.clean_score or 0))
    return ShortlistResponse(job_id=job.id, job_title=job.title, items=items)
