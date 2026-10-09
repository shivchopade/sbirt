"""Resume endpoints — report, page previews, rescore and human decision."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from fastapi.responses import Response

from app.core.errors import AppError
from app.schemas.common import ErrorResponse
from app.schemas.report import ResumeReport
from app.schemas.resumes import DecisionOut, DecisionUpdate, RescoreResponse

router = APIRouter(prefix="/resumes", tags=["resumes"], responses={404: {"model": ErrorResponse}})


def _todo() -> AppError:
    return AppError("NOT_IMPLEMENTED", "Endpoint not implemented yet", 501)


@router.get("/{resume_id}", response_model=ResumeReport)
def get_report(resume_id: UUID) -> ResumeReport:
    raise _todo()


@router.get(
    "/{resume_id}/pages/{page}/preview.png",
    response_class=Response,
    responses={
        200: {"content": {"image/png": {}}, "description": "Rendered page with flagged regions boxed"}
    },
)
def page_preview(resume_id: UUID, page: int) -> Response:
    raise _todo()


@router.post("/{resume_id}/rescore", response_model=RescoreResponse, status_code=status.HTTP_202_ACCEPTED)
def rescore(resume_id: UUID) -> RescoreResponse:
    raise _todo()


@router.patch("/{resume_id}/decision", response_model=DecisionOut)
def set_decision(resume_id: UUID, body: DecisionUpdate) -> DecisionOut:
    raise _todo()
