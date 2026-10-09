from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from app.core.config import Settings
from app.domain.document import ParsedDocument
from app.domain.enums import HiddenReason
from app.services.extraction import extract
from app.services.visibility.contrast import contrast_ratio, estimate_background, pixel_stats
from app.services.visibility.engine import apply_visibility, hidden_reason_summary


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


def analyse(data: bytes, kind: str, settings: Settings, name: str = "x") -> ParsedDocument:
    doc = extract(data, kind, file_name=name, sha256="0" * 64, settings=settings)
    return apply_visibility(doc, settings, source_bytes=data)


def analyse_fixture(fixtures_dir: Path, name: str, settings: Settings) -> ParsedDocument:
    return analyse((fixtures_dir / name).read_bytes(), name.rsplit(".", 1)[1], settings, name)


# ------------------------------------------------------------------------------------------ fixtures


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("white_text_jd.pdf", HiddenReason.LOW_CONTRAST),
        ("same_color_box.pdf", HiddenReason.LOW_CONTRAST),
        ("tiny_font.pdf", HiddenReason.TINY_FONT),
        ("squeezed_text.pdf", HiddenReason.TINY_FONT),
        ("low_opacity.pdf", HiddenReason.LOW_OPACITY),
        ("render_mode_invisible.pdf", HiddenReason.INVISIBLE_RENDER_MODE),
        ("off_page.pdf", HiddenReason.OFF_PAGE),
        ("behind_image.pdf", HiddenReason.COVERED),
        ("hidden_layer.pdf", HiddenReason.HIDDEN_LAYER),
        ("stacked_text.pdf", HiddenReason.STACKED_TEXT),
        ("clipped_text.pdf", HiddenReason.NOT_RENDERED_VISIBLY),
        ("prompt_injection_hidden.pdf", HiddenReason.LOW_CONTRAST),
        ("hidden_vanish.docx", HiddenReason.DOCX_HIDDEN),
        ("white_text.docx", HiddenReason.DOCX_HIDDEN),
        ("tiny_font.docx", HiddenReason.DOCX_HIDDEN),
        ("comment_stuffing.docx", HiddenReason.DOCX_HIDDEN),
    ],
)
def test_hidden_fixture_detected(
    fixtures_dir: Path, settings: Settings, name: str, reason: HiddenReason
) -> None:
    doc = analyse_fixture(fixtures_dir, name, settings)
    summary = hidden_reason_summary(doc)
    assert str(reason) in summary, summary
    # the trick must not leak into the visible text that gets scored
    visible = doc.visible_text
    for span in doc.hidden_spans:
        assert span.hidden_reasons, "every hidden span records why"
        if len(span.text.strip()) >= 20:
            assert span.text.strip() not in visible


@pytest.mark.parametrize(
    "name",
    [
        "clean.pdf",
        "clean.docx",
        "dark_sidebar_legit.pdf",
        "image_header_legit.pdf",
        "tailored_legit.pdf",
        "unrelated.pdf",
        "zero_width.pdf",
        "homoglyph.pdf",
    ],
)
def test_legit_fixtures_have_no_hidden_text(fixtures_dir: Path, settings: Settings, name: str) -> None:
    doc = analyse_fixture(fixtures_dir, name, settings)
    assert not doc.hidden_spans, [(s.text[:40], s.hidden_reasons, s.measurements) for s in doc.hidden_spans]
    assert not any(s.suspicious_reasons for s in doc.spans)


def test_dark_sidebar_white_text_is_visible(fixtures_dir: Path, settings: Settings) -> None:
    """False-positive guard: white text on a dark sidebar is a legit design, not hidden text."""
    doc = analyse_fixture(fixtures_dir, "dark_sidebar_legit.pdf", settings)
    white_spans = [s for s in doc.spans if s.color_rgb == (255, 255, 255) and s.text.strip()]
    assert len(white_spans) > 10
    for span in white_spans:
        assert span.visible
        assert span.background_rgb is not None and sum(span.background_rgb) < 200  # dark navy, not white
        assert span.measurements["contrast"] > 7
    assert "Python, JavaScript, SQL" in doc.visible_text


def test_white_text_jd_is_high_confidence_and_excluded(fixtures_dir: Path, settings: Settings) -> None:
    doc = analyse_fixture(fixtures_dir, "white_text_jd.pdf", settings)
    hidden = doc.hidden_spans
    assert all(s.confidence == "high" for s in hidden if len(s.text.strip()) > 10)  # rule + pixel agree
    assert all(s.measurements[str(HiddenReason.LOW_CONTRAST)] < 1.05 for s in hidden)
    assert "FinEdge" in doc.hidden_text and "FinEdge" not in doc.visible_text
    clean = analyse_fixture(Path(fixtures_dir), "clean.pdf", settings)
    assert doc.visible_text == clean.visible_text


# ------------------------------------------------------------------------------------------ synthetic cases


def _pdf(draw) -> bytes:
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, invariant=1)
    c.setFont("Helvetica", 11)
    c.drawString(60, 780, "Normal visible heading text for the page")
    draw(c)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_faux_bold_overprint_is_not_stacked(settings: Settings) -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 12)
        c.drawString(60, 700, "Senior Backend Engineer")
        c.drawString(60.35, 700, "Senior Backend Engineer")  # faux bold: same text, tiny offset

    doc = analyse(_pdf(draw), "pdf", settings)
    assert not doc.hidden_spans


def test_semi_transparent_overlay_is_not_covered(settings: Settings) -> None:
    def draw(c: Canvas) -> None:
        c.setFillColor(black)
        c.drawString(60, 700, "Highlighted achievement sentence that stays readable")
        c.setFillColor(Color(1, 0.9, 0.2, alpha=0.35))
        c.rect(55, 695, 330, 18, stroke=0, fill=1)

    doc = analyse(_pdf(draw), "pdf", settings)
    assert not doc.hidden_spans, [(s.text, s.hidden_reasons) for s in doc.hidden_spans]


def test_white_on_colored_tag_is_visible(settings: Settings) -> None:
    def draw(c: Canvas) -> None:
        c.setFillColor(HexColor("#2563EB"))
        c.roundRect(58, 694, 90, 20, 6, stroke=0, fill=1)
        c.setFillColor(white)
        c.drawString(66, 700, "Kubernetes")

    doc = analyse(_pdf(draw), "pdf", settings)
    assert not doc.hidden_spans


def test_light_gray_card_white_text_is_hidden(settings: Settings) -> None:
    def draw(c: Canvas) -> None:
        c.setFillColor(HexColor("#F4F4F4"))
        c.rect(50, 600, 480, 100, stroke=0, fill=1)
        c.setFillColor(white)
        c.drawString(60, 650, "Kafka Kubernetes Terraform Redis microservices")

    doc = analyse(_pdf(draw), "pdf", settings)
    assert {str(r) for s in doc.hidden_spans for r in s.hidden_reasons} >= {"LOW_CONTRAST"}


def test_ocr_text_layer_on_scanned_page_is_legit(fixtures_dir: Path, settings: Settings) -> None:
    """Invisible text over a scanned image is a normal OCR layer — unless nothing is drawn underneath it."""
    pdf = pymupdf.open(fixtures_dir / "scanned_image_only.pdf")
    page = pdf[0]
    page.insert_text((43, 62), "AARAV SHARMA", fontsize=19, render_mode=3)  # over the scanned name
    page.insert_text(
        (43, 790), "Kubernetes Kafka Terraform expert", fontsize=10, render_mode=3
    )  # over blank paper
    doc = analyse(pdf.tobytes(), "pdf", settings)
    by_text = {s.text.strip(): s for s in doc.spans if s.text.strip()}
    assert by_text["AARAV SHARMA"].visible
    attack = by_text["Kubernetes Kafka Terraform expert"]
    assert not attack.visible and HiddenReason.NOT_RENDERED_VISIBLY in attack.hidden_reasons
    assert HiddenReason.INVISIBLE_RENDER_MODE not in attack.hidden_reasons
    assert 1 in doc.ocr_layer_pages


def _docx(paragraph_xml: str) -> bytes:
    from docx import Document

    d = Document()
    d.add_paragraph("Regular introduction paragraph for the document.")
    buf = io.BytesIO()
    d.save(buf)
    import zipfile

    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    xml = src.read("word/document.xml").decode().replace("<w:body>", "<w:body>" + paragraph_xml, 1)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            dst.writestr(
                item, xml.encode() if item.filename == "word/document.xml" else src.read(item.filename)
            )
    return out.getvalue()


def test_docx_white_text_on_dark_shading_is_visible(settings: Settings) -> None:
    xml = (
        '<w:p><w:pPr><w:shd w:val="clear" w:color="auto" w:fill="1B2A41"/></w:pPr>'
        '<w:r><w:rPr><w:color w:val="FFFFFF"/></w:rPr><w:t>White text on a navy banner</w:t></w:r>'
        "<w:r><w:t> and automatic colour text on the same banner</w:t></w:r></w:p>"
    )
    doc = analyse(_docx(xml), "docx", settings)
    assert not doc.hidden_spans, [(s.text, s.attrs) for s in doc.hidden_spans]


def test_docx_theme_background_colour_text_is_hidden(settings: Settings) -> None:
    xml = '<w:p><w:r><w:rPr><w:color w:val="FFFFFF" w:themeColor="background1"/></w:rPr><w:t>Kafka Kubernetes Terraform</w:t></w:r></w:p>'
    doc = analyse(_docx(xml), "docx", settings)
    assert [s.attrs.get("docx_hidden") for s in doc.hidden_spans] == ["same_color"]


# ------------------------------------------------------------------------------------------ colour maths


def test_wcag_contrast_values() -> None:
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio((255, 255, 255), (255, 255, 255)) == pytest.approx(1.0)
    assert contrast_ratio((118, 118, 118), (255, 255, 255)) == pytest.approx(4.54, abs=0.05)


def test_background_estimate_handles_heavy_glyphs() -> None:
    region = np.full((20, 20, 3), 255, dtype=np.uint8)
    region[:, :12] = 0  # a heavy black icon glyph covers 60% of its own bbox
    stats = pixel_stats(region, (0, 0, 0), 48)
    assert estimate_background(stats, (0, 0, 0), 48, 0.3) == (255, 255, 255)
    uniform = np.full((20, 20, 3), 255, dtype=np.uint8)  # white text on white: nothing but the text colour
    stats = pixel_stats(uniform, (255, 255, 255), 48)
    assert estimate_background(stats, (255, 255, 255), 48, 0.3) == (255, 255, 255)
    assert stats.ink_fraction == 0
