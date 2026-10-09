"""Loads the data files in app/data (skills, section headings, phrase lists). Cached: read once per process."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@lru_cache
def load_json(name: str) -> Any:
    path = DATA_DIR / name if name.endswith(".json") and "/" in name else DATA_DIR / "lexicons" / name
    if not path.exists():
        path = DATA_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def _norm(phrase: str) -> str:
    return " ".join(phrase.lower().replace("&", "and").split())


@lru_cache
def section_headings() -> dict[str, str]:
    """heading phrase → section name."""
    return {
        _norm(p): section for section, phrases in load_json("section_headings.json").items() for p in phrases
    }


@lru_cache
def jd_section_headings() -> dict[str, str]:
    """JD heading phrase → kind (responsibility | must | nice | boilerplate)."""
    return {_norm(p): kind for kind, phrases in load_json("jd_sections.json").items() for p in phrases}


@lru_cache
def extra_stopwords() -> frozenset[str]:
    return frozenset(load_json("stopwords_extra.json")["words"])


@lru_cache
def generic_terms() -> frozenset[str]:
    return frozenset(load_json("generic_terms.json")["words"])


@lru_cache
def jd_voice_phrases() -> tuple[str, ...]:
    return tuple(load_json("jd_voice_phrases.json")["phrases"])


@lru_cache
def injection_phrases() -> tuple[str, ...]:
    return tuple(load_json("injection_phrases.json")["phrases"])


@lru_cache
def skills_data() -> dict[str, Any]:
    return json.loads((DATA_DIR / "skills.json").read_text(encoding="utf-8"))
