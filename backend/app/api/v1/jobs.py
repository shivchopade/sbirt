"""Job endpoints — thin: parse input, call a service, shape the response."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, File, UploadFile, status

from app.core.errors import AppError
from app.domain.enums import ResumeStatus
from app.schemas.common import ErrorResponse
from app.schemas.jobs import JobCreate, JobDetail, JobSummary
from app.schemas.resumes import CandidateList, ShortlistResponse, UploadResponse

router = APIRouter(
    prefix="/jobs", tags=["jobs"], responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
)


def _todo() -> AppError:
    return AppError("NOT_IMPLEMENTED", "Endpoint not implemented yet", 501)


@router.post("", response_model=JobDetail, status_code=status.HTTP_201_CREATED)
def create_job(body: JobCreate) -> JobDetail:
    raise _todo()


@router.get("", response_model=list[JobSummary])
def list_jobs() -> list[JobSummary]:
    raise _todo()


@router.get("/{job_id}", response_model=JobDetail)
def get_job(job_id: UUID) -> JobDetail:
    raise _todo()


@router.post("/{job_id}/resumes", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_resumes(job_id: UUID, files: list[UploadFile] = File(...)) -> UploadResponse:
    raise _todo()


@router.get("/{job_id}/candidates", response_model=CandidateList)
def list_candidates(
    job_id: UUID,
    status: ResumeStatus | None = None,
    sort: Literal["score", "risk", "name", "created"] = "score",
) -> CandidateList:
    raise _todo()


@router.get("/{job_id}/shortlist", response_model=ShortlistResponse)
def shortlist(job_id: UUID) -> ShortlistResponse:
    raise _todo()
