"""Resume endpoints — report, page previews, rescore and human decision."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_queue, get_resources
from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.common import ErrorResponse
from app.schemas.report import ResumeReport
from app.schemas.resumes import DecisionOut, DecisionUpdate, RescoreResponse
from app.services import resume_service
from app.services.previews import preview_png
from app.services.resources import Resources
from app.services.task_queue import TaskQueue

router = APIRouter(prefix="/resumes", tags=["resumes"], responses={404: {"model": ErrorResponse}})


@router.get("/{resume_id}", response_model=ResumeReport, summary="Full explainable report")
def get_report(resume_id: UUID, db: Session = Depends(get_db)) -> ResumeReport:
    return resume_service.build_report(db, resume_id)


@router.get(
    "/{resume_id}/pages/{page}/preview.png",
    response_class=Response,
    summary="Rendered page — hidden text boxed red, JD-copied lines boxed orange",
    responses={200: {"content": {"image/png": {}}, "description": "PNG render of the page"}},
)
async def page_preview(resume_id: UUID, page: int, db: Session = Depends(get_db)) -> Response:
    resume = resume_service.get_resume_row(db, resume_id)
    png = await run_in_threadpool(preview_png, resume, page, get_settings())
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.post(
    "/{resume_id}/rescore",
    response_model=RescoreResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Recompute with the current configuration (no re-upload)",
    responses={409: {"model": ErrorResponse}},
)
def rescore(
    resume_id: UUID,
    db: Session = Depends(get_db),
    queue: TaskQueue = Depends(get_queue),
    resources: Resources = Depends(get_resources),
) -> RescoreResponse:
    return resume_service.rescore(db, resume_id, queue, resources)


@router.patch(
    "/{resume_id}/decision", response_model=DecisionOut, summary="Human override (stored with a timestamp)"
)
def set_decision(resume_id: UUID, body: DecisionUpdate, db: Session = Depends(get_db)) -> DecisionOut:
    return resume_service.set_decision(db, resume_id, body)
