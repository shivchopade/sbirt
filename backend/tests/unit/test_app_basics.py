from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.core.config import BACKEND_ROOT, Settings
from app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(load_models=False))


def test_health_reports_components(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["models_loaded"] is False
    assert body["embedding_dim"] == 384
    assert body["status"] in {"ok", "degraded"}
    assert res.headers["X-Request-ID"]


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    res = client.get("/api/v1/nope")
    assert res.status_code == 404
    assert set(res.json()["error"]) == {"code", "message", "details"}


def test_validation_error_envelope(client: TestClient) -> None:
    res = client.post("/api/v1/jobs", json={"title": ""})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISIBILITY__LOW_CONTRAST_RATIO", "1.8")
    monkeypatch.setenv("SBERT_INPUT", "stopword_removed")
    settings = Settings()
    assert settings.visibility.low_contrast_ratio == 1.8
    assert settings.sbert_input == "stopword_removed"
    assert (
        abs(
            settings.scoring.weight_requirements
            + settings.scoring.weight_skills
            + settings.scoring.weight_overall
            - 1
        )
        < 1e-9
    )


def test_openapi_contract_is_up_to_date() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/export_openapi.py", "--check"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.db
def test_migration_creates_schema_with_hnsw_indexes(db_url: str) -> None:
    engine = create_engine(db_url)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert {"jobs", "resumes", "resume_units", "scores", "integrity_flags", "decisions", "events"} <= tables
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'")).scalar() == "vector"
        )
        idx = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_resume_units_embedding_hnsw'")
        ).scalar()
        assert "hnsw" in idx and "vector_cosine_ops" in idx
        dim = conn.execute(
            text(
                "SELECT atttypmod FROM pg_attribute WHERE attrelid='resume_units'::regclass AND attname='embedding'"
            )
        ).scalar()
        assert dim == 384
    engine.dispose()
