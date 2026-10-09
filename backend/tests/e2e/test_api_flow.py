"""End-to-end through the HTTP API: create job → upload → poll → report → preview → decide → shortlist."""

from __future__ import annotations

import dataclasses
import io
import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, text

pytestmark = [pytest.mark.db, pytest.mark.slow]


@pytest.fixture(scope="module")
def client(db_url: str, resources, tmp_path_factory: pytest.TempPathFactory):
    from app.core.config import get_settings
    from app.db.session import get_engine, get_sessionmaker
    from app.main import create_app

    storage = tmp_path_factory.mktemp("storage")
    old = {k: os.environ.get(k) for k in ("DATABASE_URL", "STORAGE_DIR")}
    os.environ["DATABASE_URL"] = db_url
    os.environ["STORAGE_DIR"] = str(storage)
    for fn in (get_settings, get_engine, get_sessionmaker):
        fn.cache_clear()
    settings = get_settings().model_copy(update={"device": "cpu"})
    app = create_app(settings, load_models=False)
    with TestClient(app) as tc:
        app.state.resources = dataclasses.replace(resources, settings=settings)
        yield tc
    for key, value in old.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    for fn in (get_settings, get_engine, get_sessionmaker):
        fn.cache_clear()


@pytest.fixture(scope="module")
def flow(client: TestClient, fixtures_dir: Path, sample_jd: str) -> dict:
    res = client.post("/api/v1/jobs", json={"title": "Backend Engineer (Python)", "jd_text": sample_jd})
    assert res.status_code == 201, res.text
    job = res.json()
    files = [
        ("files", ("clean.pdf", (fixtures_dir / "clean.pdf").read_bytes(), "application/pdf")),
        (
            "files",
            ("white_text_jd.pdf", (fixtures_dir / "white_text_jd.pdf").read_bytes(), "application/pdf"),
        ),
        ("files", ("unrelated.pdf", (fixtures_dir / "unrelated.pdf").read_bytes(), "application/pdf")),
        ("files", ("clean.docx", (fixtures_dir / "clean.docx").read_bytes(), "application/octet-stream")),
        ("files", ("encrypted.pdf", (fixtures_dir / "encrypted.pdf").read_bytes(), "application/pdf")),
        ("files", ("notes.pdf", b"just text pretending to be a PDF", "application/pdf")),
        ("files", ("clean_copy.pdf", (fixtures_dir / "clean.pdf").read_bytes(), "application/pdf")),
    ]
    res = client.post(f"/api/v1/jobs/{job['id']}/resumes", files=files)
    assert res.status_code == 202, res.text
    upload = res.json()
    deadline = time.time() + 120
    while True:  # poll like the UI does (BackgroundTasks already ran inside TestClient)
        table = client.get(f"/api/v1/jobs/{job['id']}/candidates").json()
        if table["processing"] == 0 or time.time() > deadline:
            break
        time.sleep(1)
    by_file = {row["file_name"]: row for row in table["items"]}
    return {"job": job, "upload": upload, "table": table, "rows": by_file}


def test_job_created_with_parsed_requirements(client: TestClient, flow: dict) -> None:
    job = flow["job"]
    assert job["requirement_count"] == 13 and len(job["requirements"]) == 13
    assert {"python", "fastapi", "postgresql", "docker"} <= {s["name"] for s in job["required_skills"]}
    listed = client.get("/api/v1/jobs").json()
    assert any(j["id"] == job["id"] and j["resume_count"] == 7 for j in listed)
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["title"] == "Backend Engineer (Python)"


def test_upload_accepts_good_files_and_rejects_bad_one(flow: dict) -> None:
    upload = flow["upload"]
    assert upload["accepted"] == 6 and upload["rejected"] == 1
    bad = next(r for r in upload["resumes"] if r["file_name"] == "notes.pdf")
    assert bad["status"] == "FAILED" and "PDF and DOCX" in bad["error"]


def test_candidate_table_statuses_and_decisions(flow: dict) -> None:
    rows = flow["rows"]
    assert flow["table"]["processing"] == 0 and flow["table"]["total"] == 7
    assert rows["clean.pdf"]["status"] == "DONE" and rows["clean.pdf"]["system_decision"] == "SHORTLISTED"
    assert (
        rows["clean.pdf"]["candidate_name"] == "Aarav Sharma"
        and rows["clean.pdf"]["email"] == "aarav.sharma@example.com"
    )
    assert (
        rows["white_text_jd.pdf"]["system_decision"] == "FLAGGED"
        and "LOW_CONTRAST" in rows["white_text_jd.pdf"]["flag_codes"]
    )
    assert rows["unrelated.pdf"]["system_decision"] == "NOT_SHORTLISTED"
    assert (
        rows["encrypted.pdf"]["status"] == "UNPARSEABLE"
        and rows["encrypted.pdf"]["system_decision"] == "NEEDS_REVIEW"
    )
    assert rows["notes.pdf"]["status"] == "FAILED"
    assert "DUPLICATE_FILE" in rows["clean_copy.pdf"]["flag_codes"]
    scores = [r["clean_score"] for r in flow["table"]["items"] if r["clean_score"] is not None]
    assert scores == sorted(scores, reverse=True)  # default sort = score


def test_sorting_and_filtering(client: TestClient, flow: dict) -> None:
    job_id = flow["job"]["id"]
    by_risk = client.get(f"/api/v1/jobs/{job_id}/candidates", params={"sort": "risk"}).json()["items"]
    assert by_risk[0]["file_name"] == "white_text_jd.pdf"
    failed = client.get(f"/api/v1/jobs/{job_id}/candidates", params={"status": "FAILED"}).json()
    assert [r["file_name"] for r in failed["items"]] == ["notes.pdf"]


def test_report_is_explainable(client: TestClient, flow: dict) -> None:
    rid = flow["rows"]["white_text_jd.pdf"]["resume_id"]
    report = client.get(f"/api/v1/resumes/{rid}").json()
    assert (
        report["status"] == "DONE"
        and report["decision"]["system"] == "FLAGGED"
        and report["decision"]["human"] is None
    )
    scores = report["scores"]
    assert scores["raw_score"] - scores["clean_score"] >= 10 and scores["gaming_delta"] >= 10
    assert scores["clean_score"] == pytest.approx(flow["rows"]["clean.pdf"]["clean_score"], abs=1.0)
    bd = scores["breakdown"]
    assert len(bd["requirements"]) == 13 and {
        "jd_requirement",
        "best_resume_line",
        "similarity",
        "met",
        "weight",
    } <= set(bd["requirements"][0])
    assert bd["skills"]["matched"] and bd["timings_ms"]["total"] > 0
    flags = report["integrity"]["flags"]
    assert report["integrity"]["risk_level"] == "critical" and flags[0]["severity"] == "critical"
    low_contrast = next(f for f in flags if f["code"] == "LOW_CONTRAST")
    assert {"page", "bbox", "snippet", "measured_value", "threshold"} <= set(low_contrast["evidence"])
    assert report["previews"] == [{"page": 1, "url": f"/api/v1/resumes/{rid}/pages/1/preview.png"}]
    assert report["text"]["hidden_chars"] > 1000 and "FinEdge" in report["hidden_text_excerpt"]


def test_preview_png_highlights_hidden_text(client: TestClient, flow: dict) -> None:
    rid = flow["rows"]["white_text_jd.pdf"]["resume_id"]
    res = client.get(f"/api/v1/resumes/{rid}/pages/1/preview.png")
    assert res.status_code == 200 and res.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(res.content)).convert("RGB")
    reds = sum(1 for r, g, b in img.getdata() if r > 200 and g < 80 and b < 80)
    assert reds > 500  # the hidden-text boxes are drawn
    assert client.get(f"/api/v1/resumes/{rid}/pages/9/preview.png").status_code == 404
    docx_id = flow["rows"]["clean.docx"]["resume_id"]
    assert (
        client.get(f"/api/v1/resumes/{docx_id}/pages/1/preview.png").json()["error"]["code"]
        == "PREVIEW_UNAVAILABLE"
    )


def test_human_override_shortlist_and_outbox(client: TestClient, flow: dict, db_url: str) -> None:
    rid = flow["rows"]["clean.pdf"]["resume_id"]
    res = client.patch(
        f"/api/v1/resumes/{rid}/decision",
        json={"decision": "APPROVED_FOR_INTERVIEW", "note": "Strong FastAPI work"},
    )
    assert res.status_code == 200, res.text
    decision = res.json()
    assert decision["human"] == "APPROVED_FOR_INTERVIEW" and decision["final"] == "APPROVED_FOR_INTERVIEW"
    assert decision["reviewer_note"] == "Strong FastAPI work" and [
        h["actor"] for h in decision["history"]
    ] == ["system", "human"]
    flagged = flow["rows"]["white_text_jd.pdf"]["resume_id"]
    client.patch(
        f"/api/v1/resumes/{flagged}/decision", json={"decision": "REJECTED", "note": "Hidden JD text"}
    )

    shortlist = client.get(f"/api/v1/jobs/{flow['job']['id']}/shortlist").json()
    names = {(e["candidate_name"], e["source"]) for e in shortlist["items"]}
    assert ("Aarav Sharma", "human") in names
    assert all(e["resume_id"] != flagged for e in shortlist["items"])
    assert all(e["email"] for e in shortlist["items"])

    engine = create_engine(db_url)
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM events WHERE type='candidate.shortlisted' AND payload->>'resume_id' = :r"
            ),
            {"r": rid},
        ).scalar()
    engine.dispose()
    assert count == 1  # written once, at decision time; GET /shortlist is read-only


def test_rescore_keeps_human_decision(client: TestClient, flow: dict) -> None:
    rid = flow["rows"]["clean.pdf"]["resume_id"]
    res = client.post(f"/api/v1/resumes/{rid}/rescore")
    assert res.status_code == 202
    report = client.get(f"/api/v1/resumes/{rid}").json()
    assert report["status"] == "DONE"
    assert (
        report["decision"]["human"] == "APPROVED_FOR_INTERVIEW"
        and report["decision"]["system"] == "SHORTLISTED"
    )
    bad = flow["rows"]["notes.pdf"]["resume_id"]
    assert client.post(f"/api/v1/resumes/{bad}/rescore").json()["error"]["code"] == "NOT_RESCORABLE"


def test_errors_use_the_envelope(client: TestClient, flow: dict) -> None:
    missing = client.get(f"/api/v1/resumes/{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"
    res = client.post(
        f"/api/v1/jobs/{uuid.uuid4()}/resumes", files=[("files", ("a.pdf", b"%PDF-1.4", "application/pdf"))]
    )
    assert res.status_code == 404
    bad_decision = client.patch(
        f"/api/v1/resumes/{flow['rows']['clean.pdf']['resume_id']}/decision", json={"decision": "MAYBE"}
    )
    assert bad_decision.status_code == 422 and bad_decision.json()["error"]["code"] == "VALIDATION_ERROR"


def test_raw_files_are_never_served(client: TestClient, flow: dict) -> None:
    rid = flow["rows"]["clean.pdf"]["resume_id"]
    for path in (f"/api/v1/resumes/{rid}/file", f"/api/v1/resumes/{rid}/download", "/uploads", "/var"):
        assert client.get(path).status_code in (404, 405)


def test_health(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["database"] is True and body["models_loaded"] is True and body["status"] == "ok"
