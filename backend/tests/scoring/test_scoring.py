"""Scoring behaviour: visible-only, gaming-resistant, explainable."""

from __future__ import annotations

import numpy as np
import pytest

from app.domain.document import TextUnit
from app.services.scoring.requirements import calibrate
from app.services.scoring.scorer import score_units

pytestmark = pytest.mark.slow

BASE = "clean.pdf"


def clean_of(chain, name: str) -> float:
    return chain(name)[1].clean.score


def test_hidden_jd_does_not_change_clean_score(chain) -> None:
    _, base = chain(BASE)
    _, gamed = chain("white_text_jd.pdf")
    assert gamed.clean.score == pytest.approx(base.clean.score, abs=1.0)
    assert gamed.raw.score >= gamed.clean.score + 10
    assert gamed.gaming_delta >= 10
    flag = next(f for f in gamed.flags if f.code == "SCORE_INFLATION_ATTEMPT")
    assert flag.evidence.measured_value == gamed.gaming_delta and flag.evidence.threshold == 10
    assert flag.evidence.snippet and flag.evidence.location == "hidden"


@pytest.mark.parametrize(
    "name",
    [
        "hidden_layer.pdf",
        "low_opacity.pdf",
        "off_page.pdf",
        "render_mode_invisible.pdf",
        "hidden_vanish.docx",
        "white_text.docx",
        "jd_bulk_copy.pdf",
    ],
)
def test_hidden_or_copied_jd_inflates_only_raw(chain, name: str) -> None:
    _, out = chain(name)
    assert out.clean.score == pytest.approx(clean_of(chain, BASE), abs=1.5)
    assert any(f.code == "SCORE_INFLATION_ATTEMPT" for f in out.flags)


def test_matching_resume_beats_unrelated(chain) -> None:
    assert clean_of(chain, BASE) >= clean_of(chain, "unrelated.pdf") + 40
    assert clean_of(chain, "tailored_legit.pdf") >= clean_of(chain, "unrelated.pdf") + 40
    assert clean_of(chain, BASE) >= 70


@pytest.mark.parametrize("name", ["clean.docx", "dark_sidebar_legit.pdf", "image_header_legit.pdf"])
def test_layout_and_format_do_not_change_the_score(chain, name: str) -> None:
    assert clean_of(chain, name) == pytest.approx(clean_of(chain, BASE), abs=2.0)


@pytest.mark.parametrize(
    "name", ["keyword_stuffing.pdf", "homoglyph.pdf", "zero_width.pdf", "prompt_injection_hidden.pdf"]
)
def test_content_tricks_gain_nothing(chain, name: str) -> None:
    assert clean_of(chain, name) <= clean_of(chain, BASE) + 1.0


def test_verbatim_jd_lines_are_excluded(chain) -> None:
    doc, out = chain("verbatim_jd_lines.pdf")
    copies = [u for u in doc.units if u.is_jd_copy]
    assert len(copies) == 3 and all(u.jd_match and u.jd_match["ratio"] >= 90 for u in copies)
    scored = {u.index for u in out.clean.units}
    assert not scored & {u.index for u in copies}
    assert out.raw.score > out.clean.score


def test_breakdown_is_explainable(chain) -> None:
    _, out = chain(BASE)
    clean = out.clean
    assert len(clean.requirements) == 13
    top = clean.requirements[0]
    assert top.best_resume_line and "FastAPI" in top.best_resume_line and top.met
    assert {"Python", "FastAPI", "PostgreSQL", "Docker"} <= set(clean.skills.matched)
    assert "Kubernetes" in clean.skills.missing
    assert sum(clean.weights.values()) == pytest.approx(1.0, abs=1e-3)
    implied = [d for d in clean.skills.details if d.method == "implied"]
    assert any(d.skill == "message queues" for d in implied)  # Celery/RabbitMQ ⇒ message queues


def test_unparseable_document_scores_zero(chain) -> None:
    _, out = chain("scanned_image_only.pdf")
    assert out.clean.score == 0 and out.counts["units_scored"] == 0


def _unit(i: int, text: str) -> TextUnit:
    return TextUnit(index=i, text=text, masked=text, section="experience", word_count=len(text.split()))


def test_identical_units_are_deduplicated(resources, chain) -> None:
    from app.services.scoring.scorer import select_units

    a = "Built REST APIs in FastAPI for a payments dashboard."
    b = "Deployed Docker containers to AWS ECS."
    once = select_units([_unit(0, a), _unit(1, b)], "clean", 2)
    many = select_units([_unit(0, a), _unit(1, a), _unit(2, a.upper()), _unit(3, b)], "clean", 2)
    assert len(once) == len(many) == 2
    vec = resources.embedder.encode([u.masked for u in once])
    s1 = score_units(once, vec, chain.job, chain.jv, resources, resources.settings)
    s2 = score_units(
        many,
        resources.embedder.encode([u.masked for u in many]),
        chain.job,
        chain.jv,
        resources,
        resources.settings,
    )
    assert s1.score == pytest.approx(s2.score, abs=1e-6)


def test_calibration_mapping() -> None:
    assert calibrate(0.25, 0.25, 0.75) == 0
    assert calibrate(0.50, 0.25, 0.75) == 50
    assert calibrate(0.75, 0.25, 0.75) == 100
    assert calibrate(0.95, 0.25, 0.75) == 100 and calibrate(0.0, 0.25, 0.75) == 0


def test_sbert_input_modes_both_work(chain, resources) -> None:
    alt = resources.settings.model_copy(update={"sbert_input": "stopword_removed"})
    _, natural = chain(BASE)
    _, stripped = chain(BASE, alt)
    assert 0 < stripped.clean.score <= 100
    assert stripped.clean.score != natural.clean.score


def test_embeddings_are_normalised(resources) -> None:
    vecs = resources.embedder.encode(["Python developer", "Chartered accountant"])
    assert vecs.shape == (2, resources.embedder.dim)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-4)


def test_compound_requirement_decomposition_never_hurts() -> None:
    from app.domain.job import Requirement
    from app.services.scoring.requirements import requirement_coverage

    units = [_unit(0, "Added unit tests"), _unit(1, "Reviewed pull requests")]
    unit_vecs = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    req = Requirement(index=0, text="Write tests and review code", weight=1.0, section="r", kind="must", parts=["Write tests", "review code"])
    whole = np.array([[0.6, 0.6, 0.52]], dtype=np.float32)  # the whole sentence matches each line only partly
    clauses = [np.array([[0.95, 0.1, 0.29], [0.1, 0.95, 0.29]], dtype=np.float32)]  # each clause matches one line well
    _, matches = requirement_coverage([req], whole, units, unit_vecs, floor=0.25, ceiling=0.75, met_threshold=0.55, part_vectors=clauses)
    m = matches[0]
    assert m.method == "clauses" and m.similarity == pytest.approx(0.95) and m.met
    assert m.best_resume_line == "Added unit tests | Reviewed pull requests" and len(m.parts) == 2
    weak = [np.array([[0.3, 0.1, 0.9], [0.1, 0.3, 0.9]], dtype=np.float32)]  # clauses worse than the whole sentence
    _, matches = requirement_coverage([req], whole, units, unit_vecs, floor=0.25, ceiling=0.75, met_threshold=0.55, part_vectors=weak)
    assert matches[0].method == "whole" and matches[0].similarity == pytest.approx(0.6)
