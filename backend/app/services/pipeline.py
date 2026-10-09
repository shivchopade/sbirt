"""validate → extract → visibility → preprocess → score → detect → decide (→ persist, see process_resume).

``analyze_document`` is pure (no database): the API worker, the calibration scripts and the tests all use it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date

from app.core.config import Settings
from app.domain.document import ContactInfo, ParsedDocument, SecurityReport
from app.domain.enums import ResumeStatus, Severity
from app.domain.flags import Flag
from app.domain.job import DuplicateIndex, JobContext, ParsedJob
from app.services.decision import DecisionResult, decide
from app.services.extraction import extract
from app.services.extraction.contact import extract_contact
from app.services.integrity.base import run_detectors
from app.services.integrity.file_security import security_flags
from app.services.integrity.risk import risk_score
from app.services.preprocessing import preprocess_document
from app.services.preprocessing.jd_match import mark_jd_copies
from app.services.preprocessing.jd_parser import parse_jd
from app.services.resources import Resources
from app.services.scoring.scorer import JobVectors, ScoringOutcome, compute_scores, job_vectors
from app.services.validation import inspect_file
from app.services.visibility.engine import apply_visibility

log = logging.getLogger(__name__)


@dataclass
class JobAnalysis:
    parsed: ParsedJob
    vectors: JobVectors


@dataclass
class AnalysisResult:
    status: ResumeStatus
    status_reason: str | None
    file_kind: str | None
    sha256: str
    size_bytes: int
    page_count: int | None
    doc: ParsedDocument | None
    contact: ContactInfo
    scores: ScoringOutcome | None
    flags: list[Flag]
    risk_score: int
    risk_level: Severity | None
    decision: DecisionResult
    security: SecurityReport
    timings_ms: dict[str, float] = field(default_factory=dict)
    jd_shingle_fraction: float = 0.0

    @property
    def quarantine(self) -> bool:
        return any(
            f.code in ("MALICIOUS_PDF_FEATURES", "MACRO_DOCUMENT", "EXTERNAL_TEMPLATE", "ZIP_BOMB")
            for f in self.flags
        )


def prepare_job(title: str, jd_text: str, resources: Resources) -> JobAnalysis:
    parsed = parse_jd(title, jd_text, resources.nlp, resources.settings.scoring)
    return JobAnalysis(parsed=parsed, vectors=job_vectors(parsed, resources))


def analyze_document(
    content: bytes,
    file_name: str,
    job: JobAnalysis,
    resources: Resources,
    *,
    settings: Settings | None = None,
    duplicate_index: DuplicateIndex | None = None,
    job_id: str | None = None,
    resume_id: str | None = None,
    today: date | None = None,
) -> AnalysisResult:
    settings = settings or resources.settings
    timings: dict[str, float] = {}
    clock = time.perf_counter()

    def lap(stage: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        timings[stage] = round((now - clock) * 1000, 1)
        clock = now

    validation = inspect_file(content, file_name, settings)
    lap("validate")
    if not validation.ok:
        flags = security_flags(validation.security)
        status = validation.status or ResumeStatus.FAILED
        return _early_result(validation, status, validation.error, flags, settings, timings)

    try:
        doc = extract(
            content,
            validation.kind or "pdf",
            file_name=file_name,
            sha256=validation.sha256,
            settings=settings,
        )
    except Exception as exc:
        log.exception("extraction failed")
        return _early_result(
            validation,
            ResumeStatus.FAILED,
            f"Could not read the file ({exc.__class__.__name__})",
            security_flags(validation.security),
            settings,
            timings,
        )
    doc.security = validation.security
    lap("extract")

    apply_visibility(doc, settings, source_bytes=content)
    lap("visibility")

    contact = extract_contact(doc)
    doc.contact = contact
    units = preprocess_document(doc, resources.nlp, candidate_name=contact.name)
    fraction = mark_jd_copies(units, job.parsed, settings.integrity)
    lap("preprocess")

    scores = compute_scores(units, job.parsed, job.vectors, resources, settings)
    lap("score")

    ctx = JobContext(
        parsed=job.parsed,
        settings=settings,
        resources=resources,
        job_id=job_id,
        resume_id=resume_id,
        duplicate_index=duplicate_index,
        resume_embedding=scores.clean.resume_vector,
        cache={"jd_shingle_fraction": fraction, **({"today": today} if today else {})},
    )
    flags = [*scores.flags, *run_detectors(doc, ctx)]
    flags.sort(key=lambda f: -Severity(f.severity).rank)
    lap("detect")

    visible_chars = len(doc.visible_text.replace(" ", ""))
    status, reason = ResumeStatus.DONE, None
    if visible_chars < settings.upload.min_text_chars:
        status = ResumeStatus.UNPARSEABLE
        reason = "No extractable text — looks like a scanned/image-only document" + (
            "" if settings.ocr.enabled else " (OCR disabled)"
        )
    risk, level = risk_score(flags, settings.risk)
    decision = decide(status, scores.clean.score, flags, settings.decision)
    lap("decide")
    timings["total"] = round(sum(timings.values()), 1)
    return AnalysisResult(
        status=status,
        status_reason=reason,
        file_kind=validation.kind,
        sha256=validation.sha256,
        size_bytes=validation.size_bytes,
        page_count=doc.page_count,
        doc=doc,
        contact=contact,
        scores=scores,
        flags=flags,
        risk_score=risk,
        risk_level=level,
        decision=decision,
        security=validation.security,
        timings_ms=timings,
        jd_shingle_fraction=fraction,
    )


def _early_result(
    validation,
    status: ResumeStatus,
    reason: str | None,
    flags: list[Flag],
    settings: Settings,
    timings: dict[str, float],
) -> AnalysisResult:
    risk, level = risk_score(flags, settings.risk)
    return AnalysisResult(
        status=status,
        status_reason=reason,
        file_kind=validation.kind,
        sha256=validation.sha256,
        size_bytes=validation.size_bytes,
        page_count=validation.page_count,
        doc=None,
        contact=ContactInfo(),
        scores=None,
        flags=flags,
        risk_score=risk,
        risk_level=level,
        decision=decide(status, None, flags, settings.decision),
        security=validation.security,
        timings_ms={**timings, "total": round(sum(timings.values()), 1)},
    )
