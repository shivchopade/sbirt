"""DOCX → ParsedDocument.

Walks ``word/document.xml`` in order (paragraphs, tables, content controls, text boxes), plus headers,
footers, footnotes, comments and image alt text. Each run's *effective* formatting is resolved through
docDefaults → paragraph style chain → character style chain → direct formatting, including theme colours,
``w:vanish``, highlight and shading, so the visibility engine can compare text colour with its real background.

Word stores modern text boxes twice (``mc:Choice`` + VML ``mc:Fallback``); only the Choice branch is read so
text boxes are never double-counted.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from app.domain.document import ParsedDocument, TextSpan
from app.domain.enums import SpanSource

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W, "a": A, "wp": WP, "mc": MC, "r": R}


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


HIGHLIGHT_RGB = {
    "yellow": (255, 255, 0), "green": (0, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
    "blue": (0, 0, 255), "red": (255, 0, 0), "darkBlue": (0, 0, 139), "darkCyan": (0, 139, 139),
    "darkGreen": (0, 100, 0), "darkMagenta": (139, 0, 139), "darkRed": (139, 0, 0), "darkYellow": (128, 128, 0),
    "darkGray": (169, 169, 169), "lightGray": (211, 211, 211), "black": (0, 0, 0), "white": (255, 255, 255),
}  # fmt: skip
THEME_MAP = {
    "background1": "lt1", "text1": "dk1", "background2": "lt2", "text2": "dk2", "light1": "lt1", "dark1": "dk1",
    "light2": "lt2", "dark2": "dk2", "accent1": "accent1", "accent2": "accent2", "accent3": "accent3",
    "accent4": "accent4", "accent5": "accent5", "accent6": "accent6", "hyperlink": "hlink",
    "followedHyperlink": "folHlink",
}  # fmt: skip
_TOGGLES = ("vanish", "b", "i")


def _local(el: etree._Element) -> str:
    """Local tag name that never raises (malformed/undeclared prefixes appear as 'a:tag' in recover mode)."""
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].split(":")[-1]


@dataclass
class RunProps:
    vanish: bool | None = None
    bold: bool | None = None
    italic: bool | None = None
    color: str | None = None  # hex RRGGBB or "auto"
    theme_color: str | None = None
    theme_tint: str | None = None
    theme_shade: str | None = None
    size_half_pt: int | None = None
    highlight: str | None = None
    shading: str | None = None  # hex fill
    font: str | None = None

    def merged(self, other: RunProps) -> RunProps:
        out = RunProps(**self.__dict__)
        for key, value in other.__dict__.items():
            if value is not None:
                setattr(out, key, value)
        if other.color is not None and other.theme_color is None:
            out.theme_color = None
        return out


@dataclass
class _Style:
    based_on: str | None = None
    rpr: RunProps = field(default_factory=RunProps)
    para_shading: str | None = None
    kind: str = "paragraph"


class _DocxReader:
    def __init__(self, zf: zipfile.ZipFile, default_pt: float, chars_per_page: int):
        self.zf = zf
        self.names = set(zf.namelist())
        self.default_pt = default_pt
        self.chars_per_page = chars_per_page
        self.styles: dict[str, _Style] = {}
        self.defaults = RunProps()
        self.default_para_style: str | None = None
        self.theme: dict[str, str] = {}
        self.page_bg: tuple[int, int, int] = (255, 255, 255)
        self.spans: list[TextSpan] = []
        self.line_id = 0
        self.chars_seen = 0
        self._load_theme()
        self._load_styles()

    # ---------------------------------------------------------------- loading
    def _xml(self, name: str) -> etree._Element | None:
        if name not in self.names:
            return None
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, recover=True)
        return etree.fromstring(self.zf.read(name), parser=parser)

    def _load_theme(self) -> None:
        root = self._xml("word/theme/theme1.xml")
        if root is None:
            return
        scheme = root.find(".//a:clrScheme", NS)
        if scheme is None:
            return
        for child in scheme:
            name = _local(child)
            srgb = child.find("a:srgbClr", NS)
            sysc = child.find("a:sysClr", NS)
            if srgb is not None:
                self.theme[name] = srgb.get("val", "000000")
            elif sysc is not None:
                self.theme[name] = sysc.get("lastClr", "000000")

    def _load_styles(self) -> None:
        root = self._xml("word/styles.xml")
        if root is None:
            return
        rpr_default = root.find("w:docDefaults/w:rPrDefault/w:rPr", NS)
        if rpr_default is not None:
            self.defaults = self._rpr(rpr_default)
        for st in root.findall("w:style", NS):
            sid = st.get(_w("styleId"))
            if not sid:
                continue
            based = st.find("w:basedOn", NS)
            rpr = st.find("w:rPr", NS)
            shd = st.find("w:pPr/w:shd", NS)
            self.styles[sid] = _Style(
                based_on=based.get(_w("val")) if based is not None else None,
                rpr=self._rpr(rpr) if rpr is not None else RunProps(),
                para_shading=_fill(shd),
                kind=st.get(_w("type"), "paragraph"),
            )
            if st.get(_w("type")) == "paragraph" and st.get(_w("default")) in ("1", "true"):
                self.default_para_style = sid

    def _rpr(self, el: etree._Element) -> RunProps:
        props = RunProps()
        for tag, attr in (("vanish", "vanish"), ("b", "bold"), ("i", "italic")):
            node = el.find(f"w:{tag}", NS)
            if node is not None:
                setattr(props, attr, node.get(_w("val"), "true") not in ("0", "false", "off"))
        color = el.find("w:color", NS)
        if color is not None:
            props.color = color.get(_w("val"))
            props.theme_color = color.get(_w("themeColor"))
            props.theme_tint = color.get(_w("themeTint"))
            props.theme_shade = color.get(_w("themeShade"))
        sz = el.find("w:sz", NS)
        if sz is not None and (sz.get(_w("val")) or "").isdigit():
            props.size_half_pt = int(sz.get(_w("val")))
        hl = el.find("w:highlight", NS)
        if hl is not None and hl.get(_w("val")) not in (None, "none"):
            props.highlight = hl.get(_w("val"))
        props.shading = _fill(el.find("w:shd", NS))
        fonts = el.find("w:rFonts", NS)
        if fonts is not None:
            props.font = fonts.get(_w("ascii")) or fonts.get(_w("hAnsi"))
        return props

    def _style_chain(self, sid: str | None) -> list[_Style]:
        chain: list[_Style] = []
        seen: set[str] = set()
        while sid and sid in self.styles and sid not in seen:
            seen.add(sid)
            chain.append(self.styles[sid])
            sid = self.styles[sid].based_on
        return list(reversed(chain))

    # ---------------------------------------------------------------- colours
    def resolve_rgb(self, props: RunProps) -> tuple[int, int, int] | None:
        """Explicit text colour, or None for Word's automatic colour."""
        if props.color and props.color.lower() != "auto" and re.fullmatch(r"[0-9A-Fa-f]{6}", props.color):
            return _hex(props.color)
        if props.theme_color:
            base = self.theme.get(THEME_MAP.get(props.theme_color, props.theme_color))
            if base:
                rgb = _hex(base)
                if props.theme_tint:
                    t = int(props.theme_tint, 16) / 255
                    rgb = tuple(round(c * t + 255 * (1 - t)) for c in rgb)  # type: ignore[assignment]
                if props.theme_shade:
                    s = int(props.theme_shade, 16) / 255
                    rgb = tuple(round(c * s) for c in rgb)  # type: ignore[assignment]
                return rgb
        return None

    # ---------------------------------------------------------------- walking
    def read(self) -> list[TextSpan]:
        doc = self._xml("word/document.xml")
        if doc is None:
            return []
        bg = doc.find("w:background", NS)
        if bg is not None and re.fullmatch(r"[0-9A-Fa-f]{6}", bg.get(_w("color"), "") or ""):
            self.page_bg = _hex(bg.get(_w("color")))
        body = doc.find("w:body", NS)
        rels = self._rels("word/_rels/document.xml.rels")
        for kind, target in rels.items():
            if kind.endswith("/header"):
                for t in target:
                    self._read_part(f"word/{t}", SpanSource.HEADER)
        if body is not None:
            self._walk_container(body, SpanSource.BODY, cell_fill=None)
        for kind, target in rels.items():
            if kind.endswith("/footer"):
                for t in target:
                    self._read_part(f"word/{t}", SpanSource.FOOTER)
        for part in ("word/footnotes.xml", "word/endnotes.xml"):
            self._read_part(part, SpanSource.FOOTER)
        self._read_comments()
        return self.spans

    def _rels(self, name: str) -> dict[str, list[str]]:
        root = self._xml(name)
        out: dict[str, list[str]] = {}
        if root is None:
            return out
        for rel in root.findall(f"{{{PKG_REL}}}Relationship"):
            if rel.get("TargetMode") == "External":
                continue
            out.setdefault(rel.get("Type", ""), []).append(
                rel.get("Target", "").lstrip("/").removeprefix("word/")
            )
        return out

    def _read_part(self, name: str, source: SpanSource) -> None:
        root = self._xml(name)
        if root is not None:
            self._walk_container(root, source, cell_fill=None)

    def _read_comments(self) -> None:
        root = self._xml("word/comments.xml")
        if root is None:
            return
        for comment in root.findall("w:comment", NS):
            text = " ".join(
                "".join(t.text or "" for t in p.iter(_w("t"))) for p in comment.findall(".//w:p", NS)
            ).strip()
            if text:
                self.line_id += 1
                self.spans.append(
                    TextSpan(
                        text=text,
                        page=0,
                        bbox=None,
                        source=SpanSource.COMMENT,
                        line_id=self.line_id,
                        attrs={"author": comment.get(_w("author"))},
                    )
                )

    def _walk_container(self, el: etree._Element, source: SpanSource, cell_fill: str | None) -> None:
        for child in el:
            tag = _local(child)
            if tag == "p":
                self._paragraph(child, source, cell_fill)
            elif tag == "tbl":
                for row in child.findall("w:tr", NS):
                    for cell in row.findall("w:tc", NS):
                        fill = _fill(cell.find("w:tcPr/w:shd", NS)) or cell_fill
                        self._walk_container(cell, source, fill)
            elif tag in ("sdt",):
                content = child.find("w:sdtContent", NS)
                if content is not None:
                    self._walk_container(content, source, cell_fill)
            elif tag in (
                "customXml",
                "body",
                "hdr",
                "ftr",
                "footnote",
                "endnote",
                "footnotes",
                "endnotes",
                "txbxContent",
            ):
                self._walk_container(child, source, cell_fill)

    def _paragraph(self, p: etree._Element, source: SpanSource, cell_fill: str | None) -> None:
        self.line_id += 1
        ppr = p.find("w:pPr", NS)
        pstyle_id = None
        para_fill = None
        if ppr is not None:
            ps = ppr.find("w:pStyle", NS)
            pstyle_id = ps.get(_w("val")) if ps is not None else None
            para_fill = _fill(ppr.find("w:shd", NS))
        pstyle_id = pstyle_id or self.default_para_style
        base = self.defaults
        for st in self._style_chain(pstyle_id):
            base = base.merged(st.rpr)
            para_fill = para_fill or st.para_shading
        background = para_fill or cell_fill
        self._runs(p, base, background, source, deleted=False, style=pstyle_id)

    def _runs(
        self,
        el: etree._Element,
        base: RunProps,
        background: str | None,
        source: SpanSource,
        deleted: bool,
        style: str | None,
    ) -> None:
        for child in el:
            if not isinstance(child.tag, str):
                continue
            tag = _local(child)
            if tag == "r":
                self._run(child, base, background, source, deleted, style)
            elif tag in ("hyperlink", "ins", "smartTag", "fldSimple", "customXml", "moveTo"):
                self._runs(child, base, background, source, deleted, style)
            elif tag in ("del", "moveFrom"):
                self._runs(child, base, background, source, True, style)
            elif tag == "sdt":
                content = child.find("w:sdtContent", NS)
                if content is not None:
                    self._runs(content, base, background, source, deleted, style)

    def _run(
        self,
        r: etree._Element,
        base: RunProps,
        background: str | None,
        source: SpanSource,
        deleted: bool,
        style: str | None,
    ) -> None:
        props = base
        rpr = r.find("w:rPr", NS)
        if rpr is not None:
            rstyle = rpr.find("w:rStyle", NS)
            if rstyle is not None:
                for st in self._style_chain(rstyle.get(_w("val"))):
                    props = props.merged(st.rpr)
            props = props.merged(self._rpr(rpr))
        parts: list[str] = []
        for node in r:
            if not isinstance(node.tag, str):
                continue
            tag = _local(node)
            if tag in ("t", "delText"):
                parts.append(node.text or "")
            elif tag == "tab":
                parts.append("\t")
            elif tag in ("br", "cr"):
                if parts:
                    self._emit("".join(parts), props, background, source, deleted, style)
                    parts = []
                self.line_id += 1
            elif tag in ("drawing", "pict", "AlternateContent", "object"):
                self._drawing(node)
        if parts:
            self._emit("".join(parts), props, background, source, deleted, style)

    def _drawing(self, node: etree._Element) -> None:
        """Text boxes and alt text inside a drawing; skips VML fallbacks of AlternateContent."""
        for doc_pr in node.iter(f"{{{WP}}}docPr"):
            alt = " ".join(v for v in (doc_pr.get("title"), doc_pr.get("descr")) if v).strip()
            if alt:
                self.line_id += 1
                self.spans.append(
                    TextSpan(text=alt, page=0, bbox=None, source=SpanSource.ALT_TEXT, line_id=self.line_id)
                )
        for box in node.iter(_w("txbxContent")):
            if any(_local(a) == "Fallback" for a in box.iterancestors()):
                continue
            saved = self.line_id
            self._walk_container(box, SpanSource.TEXTBOX, cell_fill=None)
            self.line_id = max(self.line_id, saved)

    def _emit(
        self,
        text: str,
        props: RunProps,
        background: str | None,
        source: SpanSource,
        deleted: bool,
        style: str | None,
    ) -> None:
        if not text:
            return
        size = (props.size_half_pt / 2) if props.size_half_pt else self.default_pt
        bg_rgb = (
            HIGHLIGHT_RGB.get(props.highlight or "")
            or (_hex(props.shading) if props.shading else None)
            or (_hex(background) if background else None)
            or self.page_bg
        )
        rgb = self.resolve_rgb(props)
        auto = rgb is None
        if rgb is None:  # Word's automatic colour contrasts with whatever is behind it
            rgb = (0, 0, 0) if _luminance(bg_rgb) > 0.18 else (255, 255, 255)
        page = (
            1 + self.chars_seen // self.chars_per_page
            if source not in (SpanSource.COMMENT, SpanSource.ALT_TEXT)
            else 0
        )
        self.chars_seen += len(text)
        self.spans.append(
            TextSpan(
                text=text,
                page=page,
                bbox=None,
                font=props.font or "",
                declared_size=size,
                effective_size=size,
                color_rgb=rgb,
                background_rgb=bg_rgb,
                source=source,
                bold=bool(props.bold),
                italic=bool(props.italic),
                line_id=self.line_id,
                attrs={
                    "vanish": bool(props.vanish),
                    "deleted": deleted,
                    "auto_color": auto,
                    "highlight": props.highlight,
                    "style": style,
                    "page_estimated": True,
                },
            )
        )


def _fill(shd: etree._Element | None) -> str | None:
    if shd is None:
        return None
    fill = shd.get(_w("fill"))
    if fill and re.fullmatch(r"[0-9A-Fa-f]{6}", fill):
        return fill
    return None


def _hex(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _luminance(rgb: tuple[int, int, int]) -> float:
    def ch(c: int) -> float:
        c2 = c / 255
        return c2 / 12.92 if c2 <= 0.03928 else ((c2 + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _core_metadata(zf: zipfile.ZipFile) -> dict[str, Any]:
    if "docProps/core.xml" not in zf.namelist():
        return {}
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    root = etree.fromstring(zf.read("docProps/core.xml"), parser=parser)
    meta: dict[str, Any] = {}
    for el in root:
        if not isinstance(el.tag, str):
            continue
        name = _local(el)
        if (
            name in ("title", "subject", "keywords", "description", "creator", "category")
            and (el.text or "").strip()
        ):
            meta["description" if name == "description" else name] = el.text.strip()
    return meta


def extract_docx(
    data: bytes, *, file_name: str, sha256: str, default_pt: float = 10.0, chars_per_page: int = 3200
) -> ParsedDocument:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        reader = _DocxReader(zf, default_pt, chars_per_page)
        spans = reader.read()
        meta = _core_metadata(zf)
    pages = max((s.page for s in spans), default=1) or 1
    parsed = ParsedDocument(
        file_name=file_name,
        file_type="docx",
        sha256=sha256,
        size_bytes=len(data),
        page_count=pages,
        page_sizes=[(595.0, 842.0)] * pages,
        spans=spans,
        metadata={k: str(v) for k, v in meta.items()},
        page_background=reader.page_bg,
    )
    sizes: dict[float, int] = {}
    for s in spans:
        if s.source == SpanSource.BODY and s.text.strip():
            sizes[s.effective_size] = sizes.get(s.effective_size, 0) + len(s.text)
    parsed.body_font_size = max(sizes, key=lambda k: sizes[k]) if sizes else default_pt
    return parsed
