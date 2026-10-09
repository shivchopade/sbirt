"""Verbatim JD-copy marking (runs before scoring: clean_score excludes copied lines; detectors report them).

Comparison uses the canonical form: lowercase, punctuation stripped, stopwords **kept**. Lines shorter than
8 words, headings, skill lists and degree names are never considered — normal tailoring is not copying.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz, process

from app.core.config import IntegritySettings
from app.domain.document import TextUnit
from app.domain.job import ParsedJob
from app.services.preprocessing.normalize import canonical

DEGREE_RE = re.compile(
    r"\b(b\.?\s?e\.?|b\.?\s?tech|m\.?\s?tech|bachelor'?s?|master'?s?|b\.?\s?sc|m\.?\s?sc|mba|ph\.?\s?d|diploma|b\.?\s?com|m\.?\s?com|b\.?\s?a\.?|m\.?\s?a\.?|b\.?\s?c\.?\s?a|m\.?\s?c\.?\s?a)\b",
    re.I,
)


def jd_sentences(job: ParsedJob, min_words: int) -> list[tuple[str, str]]:
    out = []
    for sentence in job.sentences:
        key = canonical(sentence)
        if len(key.split()) >= min_words:
            out.append((key, sentence))
    return out


def eligible(unit: TextUnit, min_words: int) -> bool:
    if unit.is_heading or unit.is_skill_list or unit.word_count < min_words:
        return False
    return not (DEGREE_RE.search(unit.text) and unit.section == "education")


def shingles(tokens: list[str], k: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def jd_shingle_fraction(units: list[TextUnit], job: ParsedJob, k: int) -> tuple[float, int, int]:
    """Share of the JD's k-word shingles (per JD sentence) that appear anywhere in the resume text."""
    jd = set().union(*(shingles(canonical(s).split(), k) for s in job.sentences)) if job.sentences else set()
    if not jd:
        return 0.0, 0, 0
    resume_tokens = canonical(" ".join(u.text for u in units)).split()
    hits = len(jd & shingles(resume_tokens, k))
    return hits / len(jd), hits, len(jd)


def mark_jd_copies(units: list[TextUnit], job: ParsedJob, integ: IntegritySettings) -> float:
    """Mark verbatim JD units; returns the JD shingle fraction (reused by the JD-copy detector)."""
    fraction = jd_shingle_fraction(units, job, integ.jd_shingle_size)[0]
    sentences = jd_sentences(job, integ.jd_line_min_words)
    if not sentences:
        return fraction
    keys = [k for k, _ in sentences]
    for unit in units:
        if not eligible(unit, integ.jd_line_min_words):
            continue
        key = canonical(unit.text)
        if len(key.split()) < integ.jd_line_min_words:
            continue
        best = process.extractOne(key, keys, scorer=fuzz.ratio)
        if best and best[1] >= integ.jd_verbatim_ratio:
            unit.is_jd_copy = True
            unit.jd_match = {
                "jd_sentence": sentences[best[2]][1],
                "ratio": round(best[1], 1),
                "method": "ratio",
            }
            continue
        # the JD sentence pasted inside a longer resume line
        n_words = len(key.split())
        for idx, jd_key in enumerate(keys):
            jd_words = len(jd_key.split())
            if jd_words > n_words or jd_words < integ.jd_partial_min_share * n_words:
                continue
            score = fuzz.partial_ratio(jd_key, key)
            if score >= integ.jd_partial_ratio:
                unit.is_jd_copy = True
                unit.jd_match = {
                    "jd_sentence": sentences[idx][1],
                    "ratio": round(score, 1),
                    "method": "partial",
                }
                break
    if fraction >= integ.jd_bulk_high:
        _mark_short_copies(units, job, integ)
    return fraction


def _mark_short_copies(units: list[TextUnit], job: ParsedJob, integ: IntegritySettings) -> None:
    """Inside a bulk copy, short JD lines pasted verbatim are excluded from clean_score as well."""
    short = jd_sentences(job, integ.jd_bulk_short_min_words)
    keys = [k for k, _ in short]
    for unit in units:
        if (
            unit.is_jd_copy
            or unit.is_heading
            or unit.is_skill_list
            or unit.word_count < integ.jd_bulk_short_min_words
        ):
            continue
        best = process.extractOne(canonical(unit.text), keys, scorer=fuzz.ratio) if keys else None
        if best and best[1] >= integ.jd_bulk_short_ratio:
            unit.is_jd_copy = True
            unit.jd_match = {
                "jd_sentence": short[best[2]][1],
                "ratio": round(best[1], 1),
                "method": "bulk_short",
            }
