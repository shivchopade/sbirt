"""Shared fixtures.

* `fixtures_dir` — generated test resumes (scripts/generate_fixtures.py), built once per session.
* `resources`    — SBERT + spaCy loaded once per session (marker: slow).
* `db_url`       — a migrated `screener_test` database on the compose Postgres (marker: db).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import BACKEND_ROOT, get_settings

GENERATED = BACKEND_ROOT / "tests" / ".generated"

os.environ.setdefault("LOG_JSON", "false")


def _test_db_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    url = make_url(get_settings().database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    url = make_url(_test_db_url())
    admin_url = url.set(database="postgres")
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}
            ).scalar()
            if exists:
                conn.execute(text(f'DROP DATABASE "{url.database}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
        admin.dispose()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Postgres not reachable ({exc.__class__.__name__}) — run `docker compose up -d db`")

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "app" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    command.upgrade(cfg, "head")
    yield url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    from scripts.generate_fixtures import generate_all

    out = GENERATED / "fixtures"
    generate_all(out)
    return out


@pytest.fixture(scope="session")
def resources():
    from app.services.resources import load_resources

    settings = get_settings().model_copy(update={"device": "cpu"})
    return load_resources(settings)


@pytest.fixture(scope="session")
def nlp_engine():
    from app.services.preprocessing.nlp import NlpEngine

    return NlpEngine()


@pytest.fixture(scope="session")
def sample_jd(fixtures_dir: Path) -> str:
    return (fixtures_dir / "jd.txt").read_text()


@pytest.fixture(scope="session")
def chain(resources, fixtures_dir: Path, sample_jd: str):
    """Runs extraction → visibility → preprocessing → JD-copy marking → scoring for a fixture (cached)."""
    from app.services.extraction import extract
    from app.services.extraction.contact import extract_contact
    from app.services.preprocessing import preprocess_document
    from app.services.preprocessing.jd_match import mark_jd_copies
    from app.services.preprocessing.jd_parser import parse_jd
    from app.services.scoring.scorer import compute_scores, job_vectors
    from app.services.visibility.engine import apply_visibility

    settings = resources.settings
    job = parse_jd("Backend Engineer (Python)", sample_jd, resources.nlp, settings.scoring)
    jv = job_vectors(job, resources)
    cache: dict[str, tuple] = {}

    def run(name: str, settings_override=None):
        st = settings_override or settings
        key = f"{name}:{st.sbert_input}"
        if key not in cache:
            data = (fixtures_dir / name).read_bytes()
            doc = extract(data, name.rsplit(".", 1)[1], file_name=name, sha256="0" * 64, settings=st)
            apply_visibility(doc, st, source_bytes=data)
            contact = extract_contact(doc)
            units = preprocess_document(doc, resources.nlp, candidate_name=contact.name)
            mark_jd_copies(units, job, st.integrity)
            cache[key] = (doc, compute_scores(units, job, jv, resources, st))
        return cache[key]

    run.job = job  # type: ignore[attr-defined]
    run.jv = jv  # type: ignore[attr-defined]
    return run
