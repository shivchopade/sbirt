"""Upload validation and file-security inspection.

* File type is decided by magic bytes, never by the extension.
* PDFs: password protection, JavaScript, /Launch, auto-run /OpenAction or /AA actions, embedded files.
  An /OpenAction that is only a destination or a GoTo (very common in legit PDFs) is NOT suspicious.
* DOCX: macros (vbaProject.bin / macro-enabled content types), remote template injection,
  zip bombs (total uncompressed size and compression ratio).
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Literal

import pymupdf

from app.core.config import Settings
from app.domain.document import SecurityReport
from app.domain.enums import ResumeStatus

FileKind = Literal["pdf", "docx"]
MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_BENIGN_ACTIONS = {"GoTo"}
_SUSPICIOUS_PDF_KEYS = {
    "/JavaScript": "javascript",
    "/JS": "javascript",
    "/Launch": "launch",
    "/EmbeddedFile": "embedded_file",
    "/RichMedia": "rich_media",
    "/SubmitForm": "submit_form",
    "/ImportData": "import_data",
}
_KEY_RE = re.compile(r"/(JavaScript|JS|Launch|EmbeddedFile|RichMedia|SubmitForm|ImportData)\b")


@dataclass
class ValidationResult:
    ok: bool
    kind: FileKind | None
    sha256: str
    size_bytes: int
    mime: str | None = None
    page_count: int | None = None
    status: ResumeStatus | None = None  # set when the file cannot be processed normally
    error_code: str | None = None
    error: str | None = None
    security: SecurityReport = field(default_factory=SecurityReport)


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_kind(data: bytes) -> FileKind | None:
    if b"%PDF-" in data[:1024]:
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile:
            return None
        if "[Content_Types].xml" in names and "word/document.xml" in names:
            return "docx"
    return None


def validate_upload(data: bytes, file_name: str, settings: Settings) -> ValidationResult:
    """Cheap checks done synchronously at upload time (type + size)."""
    result = ValidationResult(ok=False, kind=None, sha256=sha256_of(data), size_bytes=len(data))
    max_bytes = int(settings.upload.max_file_mb * 1024 * 1024)
    if not data:
        return _fail(result, "EMPTY_FILE", "The file is empty")
    if len(data) > max_bytes:
        return _fail(
            result,
            "FILE_TOO_LARGE",
            f"File is {len(data) / 1048576:.1f} MB; the limit is {settings.upload.max_file_mb:g} MB",
        )
    kind = sniff_kind(data)
    if kind is None:
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "none"
        return _fail(
            result,
            "UNSUPPORTED_TYPE",
            f"Only PDF and DOCX files are accepted (content is not PDF/DOCX; extension: .{ext})",
        )
    result.ok, result.kind, result.mime = True, kind, MIME[kind]
    return result


def inspect_file(data: bytes, file_name: str, settings: Settings) -> ValidationResult:
    """Full validation: type/size plus security inspection and page limits."""
    result = validate_upload(data, file_name, settings)
    if not result.ok:
        return result
    if result.kind == "pdf":
        _inspect_pdf(data, result, settings)
    else:
        _inspect_docx(data, result, settings)
    return result


def _fail(
    result: ValidationResult, code: str, message: str, status: ResumeStatus = ResumeStatus.FAILED
) -> ValidationResult:
    result.ok = False
    result.status = status
    result.error_code = code
    result.error = message
    return result


# ---------------------------------------------------------------- PDF


def _inspect_pdf(data: bytes, result: ValidationResult, settings: Settings) -> None:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        _fail(result, "CORRUPT_FILE", f"The PDF could not be opened ({exc.__class__.__name__})")
        return
    sec = result.security
    with doc:
        sec.encrypted = bool(doc.is_encrypted) or bool(doc.metadata and doc.metadata.get("encryption"))
        sec.needs_password = bool(doc.needs_pass)
        if sec.needs_password:
            sec.details["encryption"] = (doc.metadata or {}).get("encryption")
            _fail(
                result,
                "ENCRYPTED_FILE",
                "The PDF is password-protected and cannot be read",
                ResumeStatus.UNPARSEABLE,
            )
            result.ok = False
            return
        result.page_count = doc.page_count
        _scan_pdf_objects(doc, sec)
        if doc.page_count > settings.upload.max_pages:
            _fail(
                result,
                "TOO_MANY_PAGES",
                f"The PDF has {doc.page_count} pages; the limit is {settings.upload.max_pages}",
            )


def _scan_pdf_objects(doc: pymupdf.Document, sec: SecurityReport) -> None:
    found: dict[str, list[int]] = {}
    try:
        sec.embedded_files = doc.embfile_count()
    except Exception:
        sec.embedded_files = 0
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=False)
        except Exception:
            continue
        for match in _KEY_RE.finditer(obj):
            kind = _SUSPICIOUS_PDF_KEYS["/" + match.group(1)]
            found.setdefault(kind, []).append(xref)
    sec.javascript = "javascript" in found
    sec.launch_action = "launch" in found
    if "embedded_file" in found and not sec.embedded_files:
        sec.embedded_files = len(set(found["embedded_file"]))
    if found:
        sec.details["suspicious_objects"] = {k: sorted(set(v))[:10] for k, v in found.items()}

    # Auto-run actions on the catalog (/OpenAction) and on pages/annotations (/AA)
    catalog = doc.pdf_catalog()
    action = _resolve_action_type(doc, catalog, "OpenAction")
    if action and action not in _BENIGN_ACTIONS:
        sec.open_action = action
    aa = doc.xref_get_key(catalog, "AA")
    if aa[0] != "null":
        sec.open_action = sec.open_action or "AdditionalActions"
    for kind in ("rich_media", "submit_form", "import_data"):
        if kind in found:
            sec.open_action = sec.open_action or kind


def _resolve_action_type(doc: pymupdf.Document, xref: int, key: str) -> str | None:
    typ, val = doc.xref_get_key(xref, key)
    if typ == "null":
        return None
    if typ == "array":
        return "GoTo"  # a plain destination: [page /Fit ...]
    if typ == "xref":
        target = int(val.split()[0])
        if doc.xref_object(target).strip().startswith("["):
            return "GoTo"  # indirect destination array
        s_type, s_val = doc.xref_get_key(target, "S")
        return s_val.lstrip("/") if s_type != "null" else "Unknown"
    if typ == "dict":
        m = re.search(r"/S\s*/(\w+)", val)
        return m.group(1) if m else "Unknown"
    return "Unknown"


# ---------------------------------------------------------------- DOCX


def _inspect_docx(data: bytes, result: ValidationResult, settings: Settings) -> None:
    sec = result.security
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        _fail(result, "CORRUPT_FILE", "The DOCX archive is corrupt")
        return
    with zf:
        infos = zf.infolist()
        total = sum(i.file_size for i in infos)
        compressed = sum(i.compress_size for i in infos) or 1
        ratio = total / compressed
        sec.details["uncompressed_bytes"] = total
        sec.details["compression_ratio"] = round(ratio, 1)
        if (
            total > settings.upload.zip_max_uncompressed_mb * 1024 * 1024
            or ratio > settings.upload.zip_max_ratio
        ):
            sec.zip_bomb = True
            _fail(
                result,
                "ZIP_BOMB",
                f"Archive expands to {total / 1048576:.1f} MB (ratio {ratio:.0f}x) — refused as a possible zip bomb",
            )
            return
        names = {i.filename for i in infos}
        content_types = (
            zf.read("[Content_Types].xml").decode("utf-8", "replace")
            if "[Content_Types].xml" in names
            else ""
        )
        if any(n.lower().endswith("vbaproject.bin") for n in names) or "macroEnabled" in content_types:
            sec.macros = True
            sec.details["macro_parts"] = sorted(n for n in names if "vba" in n.lower())
        rels_name = "word/_rels/settings.xml.rels"
        if rels_name in names:
            rels = zf.read(rels_name).decode("utf-8", "replace")
            m = re.search(
                r'Type="[^"]*attachedTemplate"[^>]*Target="([^"]+)"[^>]*TargetMode="External"', rels
            ) or re.search(
                r'Target="([^"]+)"[^>]*TargetMode="External"[^>]*Type="[^"]*attachedTemplate"', rels
            )
            if m:
                sec.external_template = m.group(1)
        embeddings = sorted(n for n in names if n.startswith("word/embeddings/"))
        if embeddings:
            sec.details["embedded_objects"] = embeddings[:10]
        pages = None
        if "docProps/app.xml" in names:
            m = re.search(r"<Pages>(\d+)</Pages>", zf.read("docProps/app.xml").decode("utf-8", "replace"))
            pages = int(m.group(1)) if m else None
        result.page_count = pages
        if pages and pages > settings.upload.max_pages:
            _fail(
                result,
                "TOO_MANY_PAGES",
                f"The document has {pages} pages; the limit is {settings.upload.max_pages}",
            )
