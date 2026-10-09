"""Importing this module registers every detector (order = report order)."""

from app.services.integrity import (  # noqa: F401
    duplicates,
    file_security,
    hidden_text,
    jd_copy,
    keyword_stuffing,
    metadata_stuffing,
    prompt_injection,
    timeline,
    unicode_tricks,
)
