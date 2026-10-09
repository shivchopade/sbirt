"""Unicode normalization: NFKC, count + strip invisible characters, unify quotes/dashes/whitespace."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Characters that render as nothing. Zero-width ones are what keyword-splitting tricks use.
ZERO_WIDTH = {
    "​": "ZERO WIDTH SPACE",
    "‌": "ZERO WIDTH NON-JOINER",
    "‍": "ZERO WIDTH JOINER",
    "⁠": "WORD JOINER",
    "﻿": "ZERO WIDTH NO-BREAK SPACE",
    "᠎": "MONGOLIAN VOWEL SEPARATOR",
    "⁡": "FUNCTION APPLICATION",
    "⁢": "INVISIBLE TIMES",
    "⁣": "INVISIBLE SEPARATOR",
    "⁤": "INVISIBLE PLUS",
    "͏": "COMBINING GRAPHEME JOINER",
}
BIDI_CONTROLS = {c: unicodedata.name(c) for c in "‎‏‪‫‬‭‮⁦⁧⁨⁩"}
SOFT_HYPHEN = "­"
_STRIP = set(ZERO_WIDTH) | set(BIDI_CONTROLS) | {SOFT_HYPHEN}
_STRIP_RE = re.compile("[" + "".join(re.escape(c) for c in sorted(_STRIP)) + "]")
_TRANSLATE = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
        " ": " ",
        "\t": " ",
        "…": "...",
    }
)
_SPACES = re.compile(r"[  - 　]+")
# Cyrillic / Greek letters that render like Latin ones. Folded only inside mixed-script words ("Pythоn"),
# so the score reflects what a human reads; pure Cyrillic/Greek words are left alone.
CONFUSABLES = str.maketrans(
    "аеорсухкіјѕԁһԛԝАВЕКМНОРСТХУІЈЅοαινρτκυχΑΒΕΖΗΙΚΜΝΟΡΤΥΧ",
    "aeopcyxkijsdhqwABEKMHOPCTXYIJSoaivptkuxABEZHIKMNOPTYX",
)
_CONFUSABLE_CHARS = frozenset("аеорсухкіјѕԁһԛԝАВЕКМНОРСТХУІЈЅοαινρτκυχΑΒΕΖΗΙΚΜΝΟΡΤΥΧ")
_WORD = re.compile(r"\w+")


def fold_confusables(text: str, counter: Counter[str] | None = None) -> str:
    def fix(match: re.Match[str]) -> str:
        word = match.group(0)
        if any(c in _CONFUSABLE_CHARS for c in word) and any(
            "a" <= c <= "z" or "A" <= c <= "Z" for c in word
        ):
            if counter is not None:
                counter["confusable_words"] += 1
            return word.translate(CONFUSABLES)
        return word

    return _WORD.sub(fix, text)


BULLET_RE = re.compile(r"^\s*(?:[•●▪■◦‣∙·○◆♦►▶➢➤✓✔❖★☐□]|[-*](?=\s))\s*")


def count_invisible(text: str, counter: Counter[str] | None = None) -> Counter[str]:
    counter = counter if counter is not None else Counter()
    for ch in text:
        if ch in ZERO_WIDTH:
            counter["zero_width"] += 1
            counter[ZERO_WIDTH[ch]] += 1
        elif ch in BIDI_CONTROLS:
            counter["bidi_control"] += 1
        elif ch == SOFT_HYPHEN:
            counter["soft_hyphen"] += 1
    return counter


def normalize_text(text: str, counter: Counter[str] | None = None) -> str:
    """NFKC → count/strip invisible chars → fold mixed-script confusables → ASCII quotes/dashes → single spaces."""
    if counter is not None:
        count_invisible(text, counter)
    text = unicodedata.normalize("NFKC", text)
    text = _STRIP_RE.sub("", text)
    text = fold_confusables(text, counter)
    text = text.translate(_TRANSLATE)
    return _SPACES.sub(" ", text).strip()


def strip_bullet(text: str) -> tuple[str, bool]:
    m = BULLET_RE.match(text)
    if m:
        return text[m.end() :].strip(), True
    return text, False


def canonical(text: str) -> str:
    """Comparison form for copy detection: lowercase, punctuation stripped, stopwords kept."""
    text = normalize_text(text).lower()
    text = re.sub(r"[^\w\s+#/.]", " ", text)
    text = re.sub(r"(?<!\w)[./]|[./](?!\w)", " ", text)
    return " ".join(text.split())
