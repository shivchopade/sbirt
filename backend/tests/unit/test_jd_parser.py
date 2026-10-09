from __future__ import annotations

from app.core.config import Settings
from app.services.preprocessing.jd_parser import parse_jd


def test_sample_jd_requirements_and_weights(nlp_engine, sample_jd: str) -> None:
    job = parse_jd("Backend Engineer (Python)", sample_jd, nlp_engine, Settings().scoring)
    texts = [r.text for r in job.requirements]
    assert len(job.requirements) == 13
    assert "Design, build and maintain scalable REST APIs using Python and FastAPI." in texts
    nice = [r for r in job.requirements if r.weight == 0.5]
    assert [r.kind for r in nice] == ["nice"] * 3 and "Kubernetes" in nice[0].text
    # boilerplate and intro never become requirements
    assert not any(
        "salary" in t.lower() or "equal opportunity" in t.lower() or "ideal candidate" in t.lower()
        for t in texts
    )
    assert any(
        "equal opportunity" in s.lower() for s in job.sentences
    )  # ... but stay available for copy detection
    weights = {s.name: s.weight for s in job.skills}
    assert weights["python"] == 1.0 and weights["kafka"] == 0.5 and weights["postgresql"] == 1.0
    assert "skill:python" in job.keywords and "experience" not in job.keywords


def test_inline_nice_to_have_marker(nlp_engine) -> None:
    jd = "Requirements\n- Strong Python skills.\n- Knowledge of Terraform is a plus.\n"
    job = parse_jd("Dev", jd, nlp_engine, Settings().scoring)
    weights = {r.text: r.weight for r in job.requirements}
    assert weights["Strong Python skills."] == 1.0
    assert weights["Knowledge of Terraform is a plus."] == 0.5


def test_jd_without_headings(nlp_engine) -> None:
    jd = (
        "We need a data analyst. You will build dashboards in Power BI and write SQL queries. "
        "Experience with Python and pandas is required. Competitive salary and health insurance."
    )
    job = parse_jd("Data Analyst", jd, nlp_engine, Settings().scoring)
    assert len(job.requirements) >= 2
    assert {"power bi", "sql", "python", "pandas"} <= {s.name for s in job.skills}
    assert not any("salary" in r.text.lower() for r in job.requirements)
