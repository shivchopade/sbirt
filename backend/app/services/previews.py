"""Page previews: the rendered page with hidden text boxed red and JD-copied lines boxed orange.

Raw uploads are never served to the browser — only these PNG renders (cached per resume/page).
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import Resume
from app.services.storage import Storage

HIDDEN = (220, 38, 38)
JD_COPY = (234, 88, 12)


def preview_png(resume: Resume, page: int, settings: Settings) -> bytes:
    analysis = resume.analysis or {}
    if analysis.get("file_kind") != "pdf" or not resume.storage_path:
        raise AppError("PREVIEW_UNAVAILABLE", "Page previews are available for PDF uploads only", 404)
    if page < 1 or page > (resume.page_count or 0):
        raise AppError("NOT_FOUND", f"Page {page} does not exist", 404, {"pages": resume.page_count})
    storage = Storage(settings)
    cached = storage.preview_path(str(resume.id), page)
    if cached.exists():
        return cached.read_bytes()
    png = render_preview(Path(resume.storage_path), page, analysis.get("preview", {}), settings.preview_dpi)
    cached.write_bytes(png)
    return png


def render_preview(pdf_path: Path, page: int, regions: dict, dpi: int) -> bytes:
    with pymupdf.open(pdf_path) as doc:
        pix = doc[page - 1].get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB)
        base = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    scale = dpi / 72.0
    for key, color in (("jd_copy", JD_COPY), ("hidden", HIDDEN)):
        for region in regions.get(key, []):
            if region.get("page") != page:
                continue
            x0, y0, x1, y1 = (v * scale for v in region["bbox"])
            x0, y0 = max(0.0, x0 - 2), max(0.0, y0 - 2)
            x1, y1 = min(base.width - 1.0, x1 + 2), min(base.height - 1.0, y1 + 2)
            if x1 <= x0 or y1 <= y0:
                continue  # entirely off-page
            draw.rectangle([x0, y0, x1, y1], fill=(*color, 46), outline=(*color, 255), width=2)
    out = io.BytesIO()
    Image.alpha_composite(base, overlay).convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()
