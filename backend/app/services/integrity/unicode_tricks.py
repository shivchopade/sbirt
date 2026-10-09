from __future__ import annotations

import re
import unicodedata

from app.domain.document import ParsedDocument, TextSpan
from app.domain.enums import NON_PAGE_SOURCES, Severity
from app.domain.flags import Evidence, Flag, snippet
from app.domain.job import JobContext
from app.services.integrity.base import register
from app.services.preprocessing.normalize import CONFUSABLES, ZERO_WIDTH

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_VOWELS = set("aeiouyAEIOUY")


def _script(ch: str) -> str | None:
    if "a" <= ch.lower() <= "z":
        return "LATIN"
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return None
    if name.startswith("CYRILLIC"):
        return "CYRILLIC"
    if name.startswith("GREEK"):
        return "GREEK"
    return None


def _show_invisible(text: str) -> str:
    """Make zero-width characters visible in evidence snippets: Py⟨U+200B⟩thon."""
    return "".join(f"⟨U+{ord(c):04X}⟩" if c in ZERO_WIDTH else c for c in text)


@register
class UnicodeTricksDetector:
    """Catches character-level tricks: zero-width characters splitting or padding words (so "Py​thon" slips past a
    keyword filter but still reads as Python), homoglyph words that mix Latin with look-alike Cyrillic/Greek
    letters ("Pythоn"), and garbled text from broken or deliberately scrambled font encodings (mostly non-words —
    a sign the extracted text does not match what the page shows)."""

    name = "unicode_tricks"

    def detect(self, doc: ParsedDocument, ctx: JobContext) -> list[Flag]:
        integ = ctx.settings.integrity
        flags: list[Flag] = []
        spans = [s for s in doc.spans if s.source not in NON_PAGE_SOURCES and s.text.strip()]

        zero_width = doc.unicode_stats.get("zero_width", 0) + doc.unicode_stats.get("bidi_control", 0)
        if zero_width > integ.invisible_unicode_threshold:
            with_zw = [s for s in spans if any(c in ZERO_WIDTH for c in s.text)]
            first = with_zw[0] if with_zw else None
            flags.append(
                Flag(
                    code="INVISIBLE_UNICODE",
                    severity=Severity.HIGH if zero_width > integ.invisible_unicode_high else Severity.MEDIUM,
                    detector=self.name,
                    message=f"{zero_width} zero-width / invisible Unicode characters hidden inside the text",
                    evidence=Evidence(
                        page=first.page if first else None,
                        bbox=first.bbox if first else None,
                        snippet=snippet(_show_invisible(first.text)) if first else None,
                        measured_value=float(zero_width),
                        threshold=float(integ.invisible_unicode_threshold),
                        location="visible",
                        regions=[
                            {"page": s.page, "bbox": list(s.bbox)} for s in with_zw if s.bbox is not None
                        ][:30],
                        details={k: v for k, v in doc.unicode_stats.items() if k != "confusable_words"},
                    ),
                )
            )

        mixed = self._homoglyph_words(spans)
        if len(mixed) >= integ.homoglyph_min_words:
            words = sorted({w for w, _ in mixed})
            folded = sorted({w.translate(CONFUSABLES) for w in words})
            keyword_hit = any(
                f.lower() in {k.removeprefix("skill:") for k in ctx.parsed.keywords} for f in folded
            ) or any(f.lower() in ctx.resources.catalog for f in folded)
            first = mixed[0][1]
            flags.append(
                Flag(
                    code="HOMOGLYPHS",
                    severity=Severity.HIGH if keyword_hit else Severity.MEDIUM,
                    detector=self.name,
                    message=f"{len(words)} word(s) mix Latin with look-alike Cyrillic/Greek letters: "
                    + ", ".join(f"{w} → {w.translate(CONFUSABLES)}" for w in words[:5]),
                    evidence=Evidence(
                        page=first.page or None,
                        bbox=first.bbox,
                        snippet=snippet(first.text),
                        measured_value=float(len(words)),
                        threshold=float(integ.homoglyph_min_words),
                        location="visible" if first.visible else "hidden",
                        regions=[
                            {"page": s.page, "bbox": list(s.bbox)} for _, s in mixed if s.bbox is not None
                        ][:30],
                        details={
                            "words": words[:20],
                            "as_read": folded[:20],
                            "targets_jd_keyword": keyword_hit,
                        },
                    ),
                )
            )

        garbled = self._garbled(doc, integ.garbled_min_chars)
        if garbled is not None and garbled[0] >= integ.garbled_ratio:
            ratio, sample = garbled
            flags.append(
                Flag(
                    code="GARBLED_TEXT",
                    severity=Severity.LOW,
                    detector=self.name,
                    message=f"{ratio:.0%} of the extracted words are not real words (broken or scrambled font encoding)",
                    evidence=Evidence(
                        snippet=snippet(sample),
                        measured_value=round(ratio, 3),
                        threshold=integ.garbled_ratio,
                        location="visible",
                    ),
                )
            )
        return flags

    def _homoglyph_words(self, spans: list[TextSpan]) -> list[tuple[str, TextSpan]]:
        out: list[tuple[str, TextSpan]] = []
        for span in spans:
            for match in _WORD.finditer(span.text):
                word = match.group(0)
                scripts = {_script(c) for c in word} - {None}
                if "LATIN" in scripts and scripts & {"CYRILLIC", "GREEK"}:
                    out.append((word, span))
        return out

    def _garbled(self, doc: ParsedDocument, min_chars: int) -> tuple[float, str] | None:
        text = doc.visible_text
        if len(text) < min_chars:
            return None
        tokens = [t for t in re.findall(r"\S+", text) if any(ch.isalpha() for ch in t)]
        if not tokens:
            return None
        bad = []
        for tok in tokens:
            core = tok.strip(".,;:()[]{}'\"!?-–|/")
            if not core:
                continue
            weird = sum(1 for ch in core if unicodedata.category(ch) in ("Co", "Cn", "Cc") or ch == "�")
            letters = [ch for ch in core if ch.isalpha()]
            no_vowel = len(letters) >= 5 and core.islower() and not any(ch in _VOWELS for ch in letters)
            if weird or no_vowel or "(cid:" in tok:
                bad.append(tok)
        return len(bad) / len(tokens), " ".join(bad[:30])
