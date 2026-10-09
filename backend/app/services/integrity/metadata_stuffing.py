from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.domain.document import ParsedDocument
from app.domain.enums import Severity
from app.domain.flags import Evidence, Flag, snippet
from app.domain.job import JobContext
from app.services.integrity.base import register
from app.services.preprocessing.normalize import canonical, normalize_text

_FIELDS = ("title", "subject", "keywords", "description", "category", "xmp")


@register
class MetadataStuffingDetector:
    """Catches keyword lists or job-description text hidden in document properties (PDF title / subject /
    keywords / XMP, DOCX core properties). Some ATSs index metadata; this engine never scores it, so the flag is
    informational (low) unless the metadata carries JD sentences or many JD keywords (medium)."""

    name = "metadata_stuffing"

    def detect(self, doc: ParsedDocument, ctx: JobContext) -> list[Flag]:
        integ = ctx.settings.integrity
        jd_sents = [
            (canonical(s), s) for s in ctx.parsed.sentences if len(s.split()) >= integ.jd_line_min_words
        ]
        findings = []
        for field in _FIELDS:
            value = normalize_text(doc.metadata.get(field, ""))
            if not value:
                continue
            words = value.split()
            items = [p for p in re.split(r"[,;|]", value) if p.strip()]
            analysis = ctx.resources.nlp.analyze([value])[0]
            keywords = sorted(
                {
                    t.removeprefix("skill:").replace("_", " ")
                    for t in analysis.lexical
                    if t in ctx.parsed.keywords
                }
            )
            key = canonical(value)
            copied = [orig for jd_key, orig in jd_sents if fuzz.partial_ratio(jd_key, key) >= 90]
            too_long = len(words) > integ.metadata_max_words or len(items) > integ.metadata_max_items
            if too_long or copied or len(keywords) >= integ.metadata_min_keywords:
                findings.append(
                    {
                        "field": field,
                        "value": value,
                        "words": len(words),
                        "items": len(items),
                        "jd_keywords": keywords,
                        "jd_sentences": copied[:3],
                    }
                )
        if not findings:
            return []
        medium = any(
            f["jd_sentences"] or len(f["jd_keywords"]) >= integ.metadata_min_keywords for f in findings
        )
        worst = max(findings, key=lambda f: (len(f["jd_sentences"]), len(f["jd_keywords"]), f["words"]))
        return [
            Flag(
                code="METADATA_STUFFING",
                severity=Severity.MEDIUM if medium else Severity.LOW,
                detector=self.name,
                message=f"Document properties ({', '.join(f['field'] for f in findings)}) are stuffed with "
                + (
                    "job-description text"
                    if worst["jd_sentences"]
                    else f"{len(worst['jd_keywords'])} JD keywords"
                ),
                evidence=Evidence(
                    snippet=snippet(worst["value"]),
                    measured_value=float(len(worst["jd_keywords"])),
                    threshold=float(integ.metadata_min_keywords),
                    location="metadata",
                    details={"fields": findings, "note": "metadata never contributes to the score"},
                ),
            )
        ]
