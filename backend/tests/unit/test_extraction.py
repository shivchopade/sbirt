from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

from app.core.config import Settings
from app.domain.document import ParsedDocument
from app.domain.enums import SpanSource
from app.services.extraction import extract
from app.services.extraction.contact import extract_contact, find_phone, normalize_phone


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


def load(fixtures_dir: Path, name: str, settings: Settings) -> ParsedDocument:
    data = (fixtures_dir / name).read_bytes()
    return extract(data, name.rsplit(".", 1)[1], file_name=name, sha256="0" * 64, settings=settings)


def spans_with(doc: ParsedDocument, needle: str):
    return [s for s in doc.spans if needle in s.text]


def test_pdf_spans_carry_style_and_position(fixtures_dir: Path, settings: Settings) -> None:
    doc = load(fixtures_dir, "clean.pdf", settings)
    assert doc.page_count == 1 and doc.page_sizes[0][0] == pytest.approx(595.28, abs=0.1)
    name = spans_with(doc, "AARAV SHARMA")[0]
    assert name.declared_size == 20 and name.bold
    assert name.color_rgb == (31, 58, 95)
    assert name.bbox is not None and name.bbox[1] < 100
    assert name.seqno >= 0 and name.opacity == 1.0 and name.render_mode == 0
    assert 1 in doc.renders and doc.renders[1].shape[2] == 3
    assert "Built REST APIs in FastAPI" in doc.raw_text


def test_off_page_text_is_not_dropped(fixtures_dir: Path, settings: Settings) -> None:
    """PyMuPDF clips to the page box by default; our extraction must still see off-page text."""
    doc = load(fixtures_dir, "off_page.pdf", settings)
    off = [s for s in doc.spans if s.bbox and (s.bbox[2] < 0 or s.bbox[3] < 0)]
    assert off, "off-page spans were silently dropped"
    assert any("Kubernetes" in s.text for s in off)


def test_hidden_layer_text_is_recovered(fixtures_dir: Path, settings: Settings) -> None:
    doc = load(fixtures_dir, "hidden_layer.pdf", settings)
    layer = [s for s in doc.spans if s.source == SpanSource.HIDDEN_LAYER]
    assert layer and layer[0].layer == "Keywords"
    assert "Design, build and maintain" in " ".join(s.text for s in layer)


@pytest.mark.parametrize(
    ("name", "needle", "check"),
    [
        ("low_opacity.pdf", "Design, build", lambda s: s.opacity < 0.1),
        ("render_mode_invisible.pdf", "Design, build", lambda s: s.render_mode == 3),
        ("tiny_font.pdf", "Python FastAPI", lambda s: s.effective_size < 2),
        ("squeezed_text.pdf", "Python FastAPI", lambda s: s.effective_size < 1 and s.declared_size > 1),
        ("white_text_jd.pdf", "Model data", lambda s: s.color_rgb == (255, 255, 255)),
    ],
)
def test_pdf_hidden_attributes(fixtures_dir: Path, settings: Settings, name: str, needle: str, check) -> None:
    doc = load(fixtures_dir, name, settings)
    found = spans_with(doc, needle)
    assert found and all(check(s) for s in found), [
        (s.text[:30], s.effective_size, s.opacity, s.render_mode) for s in found
    ]


def test_draw_order_for_covering_image(fixtures_dir: Path, settings: Settings) -> None:
    doc = load(fixtures_dir, "behind_image.pdf", settings)
    text = spans_with(doc, "Kubernetes")[0]
    assert doc.images and doc.images[0].seqno > text.seqno


def test_stacked_text_keeps_both_layers(fixtures_dir: Path, settings: Settings) -> None:
    doc = load(fixtures_dir, "stacked_text.pdf", settings)
    under = spans_with(doc, "Terraform")
    over = spans_with(doc, "References available")
    assert len(under) == 1 and len(over) == 1
    assert under[0].seqno < over[0].seqno


def test_unicode_tricks_survive_extraction(fixtures_dir: Path, settings: Settings) -> None:
    assert "​" in load(fixtures_dir, "zero_width.pdf", settings).raw_text
    assert "Pythоn" in load(fixtures_dir, "homoglyph.pdf", settings).raw_text


def test_metadata_and_scanned(fixtures_dir: Path, settings: Settings) -> None:
    meta = load(fixtures_dir, "metadata_stuffing.pdf", settings).metadata
    assert "Kubernetes" in meta["keywords"] and "Design, build" in meta["subject"]
    scanned = load(fixtures_dir, "scanned_image_only.pdf", settings)
    assert not scanned.raw_text.strip() and len(scanned.images) == 1


def test_docx_effective_formatting(fixtures_dir: Path, settings: Settings) -> None:
    vanish = load(fixtures_dir, "hidden_vanish.docx", settings)
    assert any(s.attrs.get("vanish") for s in spans_with(vanish, "Design, build"))
    white = load(fixtures_dir, "white_text.docx", settings)
    span = spans_with(white, "Design, build")[0]
    assert span.color_rgb == (255, 255, 255) and span.background_rgb == (255, 255, 255)
    tiny = load(fixtures_dir, "tiny_font.docx", settings)
    assert spans_with(tiny, "Python FastAPI")[0].effective_size == 1
    comment = load(fixtures_dir, "comment_stuffing.docx", settings)
    assert any(s.source == SpanSource.COMMENT and "Design, build" in s.text for s in comment.spans)
    clean = load(fixtures_dir, "clean.docx", settings)
    assert not any(s.attrs.get("vanish") for s in clean.spans)
    assert "Built REST APIs in FastAPI" in clean.visible_text


def test_docx_textbox_not_double_counted(fixtures_dir: Path, settings: Settings) -> None:
    """Word writes text boxes twice (DrawingML choice + VML fallback); count them once."""
    box = (
        '<w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wp:anchor><wp:docPr id="1" name="Text Box 1"/>'
        "<a:graphic><a:graphicData><wps:wsp><wps:txbx><w:txbxContent><w:p><w:r><w:t>Textbox only sentence about Kafka</w:t>"
        "</w:r></w:p></w:txbxContent></wps:txbx></wps:wsp></a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice>"
        "<mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent><w:p><w:r><w:t>Textbox only sentence about Kafka</w:t>"
        "</w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback></mc:AlternateContent></w:r></w:p>"
    )
    src = zipfile.ZipFile(fixtures_dir / "clean.docx")
    xml = src.read("word/document.xml").decode()
    ns = (
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
    )
    missing = " ".join(decl for decl in ns.split() if decl.split("=")[0] not in xml)
    xml = re.sub(r"<w:document ", "<w:document " + missing + " ", xml, count=1)
    xml = xml.replace("<w:body>", "<w:body>" + box, 1)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            dst.writestr(
                item, xml.encode() if item.filename == "word/document.xml" else src.read(item.filename)
            )
    doc = extract(out.getvalue(), "docx", file_name="box.docx", sha256="0" * 64, settings=settings)
    hits = [s for s in doc.spans if "Textbox only sentence" in s.text]
    assert len(hits) == 1 and hits[0].source == SpanSource.TEXTBOX


@pytest.mark.parametrize(
    ("name", "expected_name"),
    [
        ("clean.pdf", "Aarav Sharma"),
        ("clean.docx", "Aarav Sharma"),
        ("dark_sidebar_legit.pdf", "Aarav Sharma"),
        ("tailored_legit.pdf", "Rohan Verma"),
    ],
)
def test_contact_extraction(fixtures_dir: Path, settings: Settings, name: str, expected_name: str) -> None:
    doc = load(fixtures_dir, name, settings)
    info = extract_contact(doc)
    assert info.name == expected_name
    assert info.email and info.email.endswith("@example.com")
    assert info.phone and info.phone.startswith("+91 ")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+91 98765 43210", "+91 98765 43210"),
        ("+91-9876543210", "+91 98765 43210"),
        ("09876543210", "+91 98765 43210"),
        ("9876543210", "+91 98765 43210"),
        ("12345", None),
    ],
)
def test_phone_normalisation(raw: str, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


def test_year_ranges_are_not_phones() -> None:
    assert find_phone("B.E. Computer Engineering 2017 - 2021, CGPA 8.4") is None
    assert find_phone("Call me on +91 91234 56780 any time") == "+91 91234 56780"
