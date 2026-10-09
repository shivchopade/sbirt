from __future__ import annotations

from fastapi import APIRouter, Request

from app import __version__
from app.core.config import get_settings
from app.db.session import db_is_up
from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings = get_settings()
    resources = getattr(request.app.state, "resources", None)
    database = db_is_up()
    models_loaded = resources is not None
    return HealthResponse(
        status="ok" if database and models_loaded else "degraded",
        database=database,
        models_loaded=models_loaded,
        sbert_model=settings.sbert_model,
        embedding_dim=settings.embedding_dim,
        device=getattr(resources, "device", None),
        version=__version__,
    )
