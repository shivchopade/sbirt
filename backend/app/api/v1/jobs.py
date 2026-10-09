"""Job endpoints — thin: parse input, call a service, shape the response."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.deps import get_queue, get_resources
from app.db.session import get_db
from app.domain.enums import ResumeStatus
from app.schemas.common import ErrorResponse
from app.schemas.jobs import JobCreate, JobDetail, JobSummary
from app.schemas.resumes import CandidateList, ShortlistResponse, UploadResponse
from app.services import job_service, resume_service
from app.services.resources import Resources
from app.services.task_queue import TaskQueue

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)


@router.post(
    "", response_model=JobDetail, status_code=status.HTTP_201_CREATED, summary="Create a job from a JD"
)
def create_job(
    body: JobCreate, db: Session = Depends(get_db), resources: Resources = Depends(get_resources)
) -> JobDetail:
    return job_service.create_job(db, body, resources)


@router.get("", response_model=list[JobSummary], summary="List jobs")
def list_jobs(db: Session = Depends(get_db)) -> list[JobSummary]:
    return job_service.list_jobs(db)


@router.get("/{job_id}", response_model=JobDetail, summary="Job detail with parsed requirements and skills")
def get_job(job_id: UUID, db: Session = Depends(get_db)) -> JobDetail:
    return job_service.get_job(db, job_id)


@router.post(
    "/{job_id}/resumes",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload resumes (PDF/DOCX, many files) — processed in the background",
    responses={413: {"model": ErrorResponse}},
)
async def upload_resumes(
    job_id: UUID,
    files: list[UploadFile] = File(..., description="PDF or DOCX files"),
    db: Session = Depends(get_db),
    queue: TaskQueue = Depends(get_queue),
    resources: Resources = Depends(get_resources),
) -> UploadResponse:
    limit = int(resources.settings.upload.max_file_mb * 1024 * 1024) + 1
    payload = [
        (f.filename or "resume", await f.read(limit)) for f in files
    ]  # never buffer more than the limit
    return await run_in_threadpool(resume_service.upload_resumes, db, job_id, payload, queue, resources)


@router.get("/{job_id}/candidates", response_model=CandidateList, summary="Candidate table rows")
def list_candidates(
    job_id: UUID,
    status: ResumeStatus | None = None,
    sort: Literal["score", "risk", "name", "created"] = "score",
    db: Session = Depends(get_db),
) -> CandidateList:
    return resume_service.list_candidates(db, job_id, status, sort)


@router.get(
    "/{job_id}/shortlist",
    response_model=ShortlistResponse,
    summary="Approved / shortlisted candidates with contact details (read-only, for the scheduling agent)",
)
def shortlist(job_id: UUID, db: Session = Depends(get_db)) -> ShortlistResponse:
    return resume_service.shortlist(db, job_id)
