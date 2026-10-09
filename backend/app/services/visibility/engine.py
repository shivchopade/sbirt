"""Visibility engine: marks every span visible or hidden and records every reason.

PDF:  OCR-layer detection → attribute/geometry rules → independent pixel check (+ optional OCR).
DOCX: effective-formatting rules (+ optional LibreOffice DOCX→PDF so the visual checks also run).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import Settings
from app.domain.document import ParsedDocument
from app.domain.enums import HiddenReason
from app.services.visibility.rules import apply_docx_rules, apply_pdf_rules, detect_ocr_layer_pages
from app.services.visibility.visual_check import apply_ocr, apply_pixel_checks

log = logging.getLogger(__name__)


def apply_visibility(
    doc: ParsedDocument, settings: Settings, *, source_bytes: bytes | None = None
) -> ParsedDocument:
    vis = settings.visibility
    if doc.file_type == "pdf":
        doc.ocr_layer_pages = detect_ocr_layer_pages(doc, vis)
        apply_pdf_rules(doc, vis)
        apply_pixel_checks(doc, vis)
        if settings.ocr.enabled:
            apply_ocr(doc, settings)
    else:
        apply_docx_rules(doc, vis)
        if settings.docx.convert_to_pdf and source_bytes is not None:
            _cross_check_docx(doc, source_bytes, settings)
    return doc


def _cross_check_docx(doc: ParsedDocument, data: bytes, settings: Settings) -> None:
    """Render the DOCX with LibreOffice and hide DOCX runs whose text the PDF visual checks call hidden."""
    soffice = shutil.which(settings.docx.soffice_path)
    if not soffice:
        doc.warnings.append("DOCX__CONVERT_TO_PDF is on but LibreOffice (soffice) was not found")
        return
    from app.services.extraction.pdf import extract_pdf

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.docx"
        src.write_bytes(data)
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
                check=True,
                timeout=60,
                capture_output=True,
            )
            pdf_bytes = (Path(tmp) / "input.pdf").read_bytes()
        except (subprocess.SubprocessError, OSError) as exc:
            doc.warnings.append(f"DOCX→PDF conversion failed: {exc.__class__.__name__}")
            return
    rendered = extract_pdf(
        pdf_bytes, file_name=doc.file_name, sha256=doc.sha256, render_dpi=settings.visibility.render_dpi
    )
    apply_pdf_rules(rendered, settings.visibility)
    apply_pixel_checks(rendered, settings.visibility)
    hidden = {
        " ".join(s.text.split()).lower(): s.hidden_reasons
        for s in rendered.spans
        if not s.visible and s.text.strip()
    }
    for span in doc.spans:
        key = " ".join(span.text.split()).lower()
        if span.visible and key and key in hidden:
            for reason in hidden[key]:
                span.hide(reason)
            span.attrs["docx_cross_check"] = True
    doc.page_count = rendered.page_count
    doc.page_sizes = rendered.page_sizes
    doc.renders = rendered.renders


def hidden_reason_summary(doc: ParsedDocument) -> dict[str, int]:
    counts: dict[str, int] = {}
    for span in doc.hidden_spans:
        for reason in span.hidden_reasons:
            counts[str(reason)] = counts.get(str(reason), 0) + 1
    return counts


__all__ = ["HiddenReason", "apply_visibility", "hidden_reason_summary"]
