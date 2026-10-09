"""Extraction entry point: bytes → ParsedDocument (PDF via PyMuPDF, DOCX via lxml)."""

from __future__ import annotations

from app.core.config import Settings
from app.domain.document import ParsedDocument
from app.services.extraction.docx import extract_docx
from app.services.extraction.pdf import extract_pdf


def extract(data: bytes, kind: str, *, file_name: str, sha256: str, settings: Settings) -> ParsedDocument:
    if kind == "pdf":
        return extract_pdf(
            data, file_name=file_name, sha256=sha256, render_dpi=settings.visibility.render_dpi
        )
    if kind == "docx":
        return extract_docx(
            data,
            file_name=file_name,
            sha256=sha256,
            default_pt=settings.docx.default_font_pt,
            chars_per_page=settings.docx.chars_per_page,
        )
    raise ValueError(f"unsupported kind: {kind}")
