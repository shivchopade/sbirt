"""Jobs: create (parse + embed the JD once), list, detail, and the cached analysis every resume reuses."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models import Job, Resume
from app.domain.job import ParsedJob
from app.schemas.jobs import JobCreate, JobDetail, JobSummary, RequirementOut, SkillOut
from app.services.pipeline import JobAnalysis, prepare_job
from app.services.resources import Resources
from app.services.scoring.scorer import job_vectors

_cache: OrderedDict[uuid.UUID, JobAnalysis] = OrderedDict()
_lock = threading.Lock()


def create_job(session: Session, body: JobCreate, resources: Resources) -> JobDetail:
    analysis = prepare_job(body.title.strip(), body.jd_text, resources)
    parsed = analysis.parsed
    job = Job(
        title=body.title.strip(),
        jd_text=body.jd_text,
        requirements=[r.__dict__ for r in parsed.requirements],
        required_skills=[s.__dict__ for s in parsed.skills],
        jd_analysis=parsed.to_dict(),
        jd_embedding=analysis.vectors.jd_mean.tolist() if analysis.vectors.jd_mean is not None else None,
        embedding_model=resources.settings.sbert_model,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    with _lock:
        _cache[job.id] = analysis
    return _detail(job, 0, 0)


def job_analysis(job: Job, resources: Resources) -> JobAnalysis:
    with _lock:
        cached = _cache.get(job.id)
        if cached is not None:
            _cache.move_to_end(job.id)
            return cached
    parsed = ParsedJob.from_dict(job.jd_text, job.jd_analysis)
    analysis = JobAnalysis(parsed=parsed, vectors=job_vectors(parsed, resources))
    with _lock:
        _cache[job.id] = analysis
        while len(_cache) > 64:
            _cache.popitem(last=False)
    return analysis


def get_job_row(session: Session, job_id: uuid.UUID) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise NotFoundError("Job", job_id)
    return job


def _counts(session: Session, job_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
    if not job_ids:
        return {}
    busy = case((Resume.status.in_(["PENDING", "PROCESSING"]), 1), else_=0)
    rows = session.execute(
        select(Resume.job_id, func.count(Resume.id), func.coalesce(func.sum(busy), 0))
        .where(Resume.job_id.in_(job_ids))
        .group_by(Resume.job_id)
    ).all()
    return {r[0]: (int(r[1]), int(r[2])) for r in rows}


def _summary(job: Job, total: int, busy: int) -> JobSummary:
    return JobSummary(
        id=job.id,
        title=job.title,
        created_at=job.created_at,
        requirement_count=len(job.requirements or []),
        skill_count=len(job.required_skills or []),
        resume_count=total,
        processing_count=busy,
    )


def _detail(job: Job, total: int, busy: int) -> JobDetail:
    return JobDetail(
        **_summary(job, total, busy).model_dump(),
        jd_text=job.jd_text,
        requirements=[RequirementOut(**r) for r in job.requirements or []],
        required_skills=[SkillOut(**s) for s in job.required_skills or []],
        keywords=sorted(
            k.removeprefix("skill:").replace("_", " ") for k in (job.jd_analysis or {}).get("keywords", [])
        ),
    )


def list_jobs(session: Session) -> list[JobSummary]:
    jobs = session.scalars(select(Job).order_by(Job.created_at.desc())).all()
    counts = _counts(session, [j.id for j in jobs])
    return [_summary(j, *counts.get(j.id, (0, 0))) for j in jobs]


def get_job(session: Session, job_id: uuid.UUID) -> JobDetail:
    job = get_job_row(session, job_id)
    return _detail(job, *_counts(session, [job.id]).get(job.id, (0, 0)))
