from __future__ import annotations

import re

import numpy as np

from app.domain.document import ParsedDocument, TextUnit
from app.domain.enums import Severity
from app.domain.flags import Evidence, Flag, snippet
from app.domain.job import JobContext
from app.services.integrity.base import first_location, location_of, regions_of, register
from app.services.preprocessing.jd_match import eligible, jd_shingle_fraction
from app.services.preprocessing.lexicons import jd_voice_phrases
from app.services.preprocessing.normalize import canonical


@register
class JdCopyDetector:
    """Catches resumes that paste the job description instead of describing the candidate: lines copied
    verbatim (rapidfuzz ratio ≥ 90), bulk copying measured with 8-word shingles, lightly reworded copies (SBERT
    cosine ≥ 0.92 *and* token overlap ≥ 0.7) and phrases that only make sense in a JD ("the ideal candidate",
    "you will", "join our team"). Runs on visible and hidden text; skill lists, headings, degree names and lines
    under 8 words are never flagged, so normal tailoring is not punished."""

    name = "jd_copy"

    def detect(self, doc: ParsedDocument, ctx: JobContext) -> list[Flag]:
        integ = ctx.settings.integrity
        flags: list[Flag] = []
        units = [u for u in doc.units if not u.is_heading]

        verbatim = [
            u
            for u in units
            if u.is_jd_copy and u.jd_match and u.jd_match.get("method") in ("ratio", "partial")
        ]
        if verbatim:
            page, bbox = first_location(verbatim)
            flags.append(
                Flag(
                    code="VERBATIM_JD_LINE",
                    severity=Severity.HIGH,
                    detector=self.name,
                    message=f"{len(verbatim)} resume line(s) copied word-for-word from the job description ({location_of(verbatim)} text)",
                    evidence=Evidence(
                        page=page,
                        bbox=bbox,
                        snippet=snippet(verbatim[0].text),
                        measured_value=max(u.jd_match["ratio"] for u in verbatim),  # type: ignore[index]
                        threshold=integ.jd_verbatim_ratio,
                        location=location_of(verbatim),
                        regions=regions_of(verbatim),
                        details={
                            "lines": [
                                {
                                    "resume": u.text,
                                    "jd": u.jd_match["jd_sentence"],
                                    "ratio": u.jd_match["ratio"],
                                    "visible": u.visible,
                                }  # type: ignore[index]
                                for u in verbatim[:10]
                            ]
                        },
                    ),
                )
            )

        fraction, hits, total = jd_shingle_fraction(units, ctx.parsed, integ.jd_shingle_size)
        if total and fraction >= integ.jd_bulk_high:
            visible_fraction = jd_shingle_fraction(
                [u for u in units if u.visible], ctx.parsed, integ.jd_shingle_size
            )[0]
            critical = fraction >= integ.jd_bulk_critical
            copied = [u for u in units if u.is_jd_copy]
            page, bbox = first_location(copied)
            flags.append(
                Flag(
                    code="JD_BULK_COPY",
                    severity=Severity.CRITICAL if critical else Severity.HIGH,
                    detector=self.name,
                    message=f"{fraction:.0%} of the job description's 8-word phrases appear in the resume",
                    evidence=Evidence(
                        page=page,
                        bbox=bbox,
                        snippet=snippet(" | ".join(u.text for u in copied[:4])) if copied else None,
                        measured_value=round(fraction, 4),
                        threshold=integ.jd_bulk_critical if critical else integ.jd_bulk_high,
                        location="visible"
                        if visible_fraction >= integ.jd_bulk_high
                        else ("hidden" if visible_fraction == 0 else "mixed"),
                        regions=regions_of(copied),
                        details={
                            "shingles_matched": hits,
                            "shingles_total": total,
                            "visible_fraction": round(visible_fraction, 4),
                        },
                    ),
                )
            )

        near = self._near_verbatim(units, ctx)
        if near:
            page, bbox = first_location([u for u, _ in near])
            flags.append(
                Flag(
                    code="NEAR_VERBATIM_JD",
                    severity=Severity.MEDIUM,
                    detector=self.name,
                    message=f"{len(near)} line(s) are lightly reworded copies of JD sentences",
                    evidence=Evidence(
                        page=page,
                        bbox=bbox,
                        snippet=snippet(near[0][0].text),
                        measured_value=max(m["cosine"] for _, m in near),
                        threshold=integ.jd_near_cosine,
                        location=location_of([u for u, _ in near]),
                        regions=regions_of([u for u, _ in near]),
                        details={
                            "lines": [{"resume": u.text, **m} for u, m in near[:10]],
                            "overlap_threshold": integ.jd_near_overlap,
                        },
                    ),
                )
            )

        voice = self._voice(units)
        if voice:
            page, bbox = first_location([u for u, _ in voice])
            phrases = sorted({p for _, ps in voice for p in ps})
            flags.append(
                Flag(
                    code="JD_VOICE_PHRASES",
                    severity=Severity.MEDIUM,
                    detector=self.name,
                    message=f"Job-description language in the resume: {', '.join(repr(p) for p in phrases[:4])}",
                    evidence=Evidence(
                        page=page,
                        bbox=bbox,
                        snippet=snippet(voice[0][0].text),
                        measured_value=float(len(voice)),
                        threshold=1.0,
                        location=location_of([u for u, _ in voice]),
                        regions=regions_of([u for u, _ in voice]),
                        details={"phrases": phrases},
                    ),
                )
            )
        return flags

    def _near_verbatim(self, units: list[TextUnit], ctx: JobContext) -> list[tuple[TextUnit, dict]]:
        integ = ctx.settings.integrity
        candidates = [u for u in units if not u.is_jd_copy and eligible(u, integ.jd_line_min_words)]
        jd = [s for s in ctx.parsed.sentences if len(s.split()) >= integ.jd_line_min_words]
        if not candidates or not jd:
            return []
        emb = ctx.resources.embedder
        jd_vecs = ctx.cache.get("jd_sentence_vectors")
        if jd_vecs is None:
            jd_vecs = ctx.cache["jd_sentence_vectors"] = emb.encode(jd)
        unit_vecs = emb.encode([u.text for u in candidates])
        sims = unit_vecs @ np.asarray(jd_vecs).T
        out: list[tuple[TextUnit, dict]] = []
        for i, unit in enumerate(candidates):
            j = int(np.argmax(sims[i]))
            cosine = float(sims[i, j])
            if cosine < integ.jd_near_cosine:
                continue
            jd_tokens = set(canonical(jd[j]).split())
            overlap = len(jd_tokens & set(canonical(unit.text).split())) / max(1, len(jd_tokens))
            if overlap >= integ.jd_near_overlap:
                out.append((unit, {"jd": jd[j], "cosine": round(cosine, 4), "overlap": round(overlap, 3)}))
        return out

    def _voice(self, units: list[TextUnit]) -> list[tuple[TextUnit, list[str]]]:
        patterns = [(p, re.compile(r"\b" + re.escape(canonical(p)) + r"\b")) for p in jd_voice_phrases()]
        out = []
        for unit in units:
            if unit.is_jd_copy or unit.is_skill_list:
                continue
            key = canonical(unit.text)
            found = [p for p, rx in patterns if rx.search(key)]
            if found:
                out.append((unit, found))
        return out
