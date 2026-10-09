from __future__ import annotations

import re

import numpy as np

from app.domain.document import ParsedDocument, TextUnit
from app.domain.enums import Severity
from app.domain.flags import Evidence, Flag, snippet
from app.domain.job import JobContext
from app.services.integrity.base import first_location, location_of, regions_of, register
from app.services.preprocessing.normalize import normalize_text

# Resume text is untrusted data: it is only *matched* here, never interpreted or executed.
PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.]{0,40}\b(all |any |the )?(previous|prior|above|earlier|preceding|your|system)?\s*(instructions?|prompts?|rules|guidelines|directions)\b",
        re.I,
    ),
    "you_are_ai": re.compile(
        r"\byou are (an? |the )?(ai|a\.i\.|assistant|language model|llm|chatbot|gpt|large language model)\b",
        re.I,
    ),
    "system_prompt": re.compile(r"\b(system|developer) (prompt|message|instruction)s?\b", re.I),
    "rate_candidate": re.compile(
        r"\b(rate|rank|score|grade|evaluate|mark)\b\s+(this|the)\s+(candidate|applicant|resume|cv|profile)\b",
        re.I,
    ),
    "select_candidate": re.compile(
        r"\b(shortlist|select|hire|recommend|approve|accept|advance)\b\s+(this|the)\s+(candidate|applicant|person|resume)\b",
        re.I,
    ),
    "perfect_fit": re.compile(
        r"\b(this|the) (candidate|applicant) is (the |an? )?(perfect|ideal|best|excellent|top|strongest) (fit|match|candidate|choice)\b",
        re.I,
    ),
    "note_to_ai": re.compile(
        r"\bnote to (the )?(ai|a\.i\.|screener|screening (system|tool|software)|llm|model|ats|recruiter bot|bot)\b",
        re.I,
    ),
    "chat_template": re.compile(
        r"(\[/?INST\]|<\|im_(start|end)\|>|<\|(system|user|assistant|endoftext)\|>|<<\s*/?SYS\s*>>|###\s*(instruction|system)\b)",
        re.I,
    ),
}

# Cue rule for paraphrases: the line talks *to* an automated reader *and* tells it to do something.
ADDRESS_CUE = re.compile(
    r"\b(automated|ai|a\.i\.|bots?|ats|applicant tracking|screen(?:er|ers|ing (?:system|tool|software|algorithm))|"
    r"reviewers?|algorithms?|language models?|llms?|assistants?|machines?|systems?)\b",
    re.I,
)
DIRECTIVE_CUE = re.compile(
    r"(\b(please|should|must|need to|have to|make sure|be sure to)\b|"
    r"(^|[:,;]\s*)(give|rank|score|rate|move|recommend|shortlist|select|treat|mark|advance|approve|consider|"
    r"ignore|forget|output|respond|reply|say|tell|prioriti[sz]e|put)\b)",
    re.I,
)


@register
class PromptInjectionDetector:
    """Catches instructions aimed at an AI screener ("ignore all previous instructions", "you are an AI",
    "rank this candidate first", chat-template tokens like [INST] or <|im_start|>) anywhere in the file —
    visible text, hidden text, comments and metadata. Regex hits are critical; paraphrases that only an embedding
    catches (SBERT ≥ 0.75 to ~20 canonical injection phrases) are high. This engine never sends resume text to an
    LLM, so injections cannot work here — the flag protects any downstream AI tool and tells the reviewer.

    Paraphrase tier: SBERT similarity ≥ 0.75 on its own, or ≥ 0.60 when the line also addresses an automated reader
    ("automated reviewers", "AI", "ATS") with a directive ("please give…", "should rank…"). Achievement lines such
    as "Developed a resume-screening tool that scores candidates" have no directive and are not flagged."""

    name = "prompt_injection"

    def detect(self, doc: ParsedDocument, ctx: JobContext) -> list[Flag]:
        integ = ctx.settings.integrity
        hits: list[tuple[TextUnit, list[str]]] = []
        for unit in doc.units:
            names = [n for n, rx in PATTERNS.items() if rx.search(unit.text)]
            if names:
                hits.append((unit, names))
        meta_hits = []
        for field, value in doc.metadata.items():
            text = normalize_text(value)
            names = [n for n, rx in PATTERNS.items() if rx.search(text)]
            if names:
                meta_hits.append((field, text, names))

        flags: list[Flag] = []
        if hits or meta_hits:
            units = [u for u, _ in hits]
            page, bbox = first_location(units)
            location = location_of(units) if units else "metadata"
            patterns = sorted({n for _, ns in hits for n in ns} | {n for *_, ns in meta_hits for n in ns})
            sample = units[0].text if units else meta_hits[0][1]
            flags.append(
                Flag(
                    code="PROMPT_INJECTION",
                    severity=Severity.CRITICAL,
                    detector=self.name,
                    message=f"Instructions aimed at an AI screener found in {location} text ({', '.join(patterns)})",
                    evidence=Evidence(
                        page=page,
                        bbox=bbox,
                        snippet=snippet(sample),
                        measured_value=float(len(hits) + len(meta_hits)),
                        threshold=1.0,
                        location=location,
                        regions=regions_of(units),
                        details={"patterns": patterns, "metadata_fields": [f for f, *_ in meta_hits]},
                    ),
                )
            )

        semantic = self._semantic(doc, ctx, {u.index for u, _ in hits})
        if semantic:
            units = [u for u, _, _ in semantic]
            cue_only = all(rule == "semantic+cue" for _, _, rule in semantic)
            page, bbox = first_location(units)
            flags.append(
                Flag(
                    code="PROMPT_INJECTION_PARAPHRASE",
                    severity=Severity.HIGH,
                    detector=self.name,
                    message=f"{len(units)} line(s) read like paraphrased instructions to an AI screener",
                    evidence=Evidence(
                        page=page,
                        bbox=bbox,
                        snippet=snippet(units[0].text),
                        measured_value=max(s for _, s, _ in semantic),
                        threshold=integ.injection_cue_threshold
                        if cue_only
                        else integ.injection_semantic_threshold,
                        location=location_of(units),
                        regions=regions_of(units),
                        details={
                            "lines": [
                                {"text": u.text, "similarity": s, "rule": r} for u, s, r in semantic[:5]
                            ],
                            "rule": "similarity ≥ cue threshold + addressed to an automated reader with a directive"
                            if cue_only
                            else "similarity ≥ semantic threshold",
                        },
                    ),
                )
            )
        return flags

    def _semantic(
        self, doc: ParsedDocument, ctx: JobContext, skip: set[int]
    ) -> list[tuple[TextUnit, float, str]]:
        integ = ctx.settings.integrity
        units = [
            u
            for u in doc.units
            if u.index not in skip and not u.is_heading and u.word_count >= integ.injection_min_words
        ]
        if not units:
            return []
        vectors = ctx.resources.embedder.encode([u.text for u in units])
        sims = vectors @ np.asarray(ctx.resources.injection_vectors).T
        best = sims.max(axis=1)
        out = []
        for unit, b in zip(units, best, strict=True):
            score = float(b)
            cued = bool(ADDRESS_CUE.search(unit.text) and DIRECTIVE_CUE.search(unit.text))
            if score >= integ.injection_semantic_threshold:
                out.append((unit, round(score, 4), "semantic"))
            elif cued and score >= integ.injection_cue_threshold:
                out.append((unit, round(score, 4), "semantic+cue"))
        return out
