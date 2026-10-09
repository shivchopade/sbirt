from __future__ import annotations

from app.domain.document import ParsedDocument, SecurityReport
from app.domain.enums import Severity
from app.domain.flags import Evidence, Flag
from app.domain.job import JobContext
from app.services.integrity.base import register


def security_flags(sec: SecurityReport, *, detector: str = "file_security") -> list[Flag]:
    """Flags from the validation/security inspection (also used for files that fail before extraction)."""
    flags: list[Flag] = []
    if sec.needs_password:
        flags.append(
            Flag(
                "ENCRYPTED_FILE",
                Severity.MEDIUM,
                detector,
                "The PDF is password-protected, so its content cannot be checked — ask the candidate for an unlocked copy",
                Evidence(
                    location="file",
                    measured_value=1.0,
                    threshold=1.0,
                    details={"encryption": sec.details.get("encryption")},
                ),
            )
        )
    if sec.malicious_pdf:
        found = [
            name
            for name, on in (
                ("JavaScript", sec.javascript),
                ("Launch action", sec.launch_action),
                (f"auto-run action ({sec.open_action})", bool(sec.open_action)),
                (f"{sec.embedded_files} embedded file(s)", sec.embedded_files > 0),
            )
            if on
        ]
        flags.append(
            Flag(
                "MALICIOUS_PDF_FEATURES",
                Severity.CRITICAL,
                detector,
                f"PDF contains active content: {', '.join(found)}. The file is quarantined and never served.",
                Evidence(
                    location="file",
                    snippet=", ".join(found),
                    measured_value=float(len(found)),
                    threshold=1.0,
                    details=sec.details,
                ),
            )
        )
    if sec.macros:
        flags.append(
            Flag(
                "MACRO_DOCUMENT",
                Severity.CRITICAL,
                detector,
                "Word document contains VBA macros. The file is quarantined and never served.",
                Evidence(location="file", measured_value=1.0, threshold=1.0, details=sec.details),
            )
        )
    if sec.external_template:
        flags.append(
            Flag(
                "EXTERNAL_TEMPLATE",
                Severity.CRITICAL,
                detector,
                "Word document loads a remote template (a known malware delivery technique)",
                Evidence(location="file", snippet=sec.external_template, measured_value=1.0, threshold=1.0),
            )
        )
    if sec.zip_bomb:
        flags.append(
            Flag(
                "ZIP_BOMB",
                Severity.CRITICAL,
                detector,
                "Archive expands to an abnormal size (zip bomb) — refused",
                Evidence(
                    location="file",
                    measured_value=float(sec.details.get("compression_ratio", 0)),
                    threshold=None,
                    details=sec.details,
                ),
            )
        )
    return flags


@register
class FileSecurityDetector:
    """Catches dangerous or unreadable files: password protection, PDF JavaScript / Launch / auto-run actions /
    embedded files, Word macros, remote templates, zip bombs — and image-only documents with no extractable text
    (needs a human or OCR). Nothing in a resume is ever executed; dangerous files are quarantined."""

    name = "file_security"

    def detect(self, doc: ParsedDocument, ctx: JobContext) -> list[Flag]:
        flags = security_flags(doc.security, detector=self.name)
        visible_chars = len(doc.visible_text.replace(" ", ""))
        if visible_chars < ctx.settings.upload.min_text_chars:
            flags.append(
                Flag(
                    "SCANNED_DOCUMENT",
                    Severity.LOW,
                    self.name,
                    f"Only {visible_chars} readable characters — the file looks like a scanned image"
                    + ("" if ctx.settings.ocr.enabled else " (OCR is disabled)"),
                    Evidence(
                        location="file",
                        measured_value=float(visible_chars),
                        threshold=float(ctx.settings.upload.min_text_chars),
                        details={"images": len(doc.images), "pages": doc.page_count},
                    ),
                )
            )
        return flags
