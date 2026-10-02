"""Word (.docx) parser.

Unlike PDFs, a Word file states its structure, so there's very little guessing:

* Headings: a paragraph is a heading if its style (or a style it's based on)
  has an outline level. Built-in "Heading 1-6" have one, and so do custom styles
  based on them (e.g. "Law Heading 2" is based on "Heading 2"). Level n becomes
  style key "word_hn", mapped to Google Docs' Heading n by default (Settings).
* Bullets and numbered lists come from Word's own list definitions
  (numbering.xml): the list level (0, 1, 2…) is the nesting level. For numbered
  lists the number Word would display ("1.", "(a)", "iii.") is worked out and
  kept as literal text, like numbering in PDFs.
* Inline formatting: bold, italic, underline, superscript/subscript, text
  colour and highlight. Formatting inherited from styles counts too (e.g. a
  character style that makes text bold). Colours set by styles (like blue
  heading themes) are ignored: only colour applied to the text itself is kept,
  because that's the meaningful kind.
* Tables become tables (merged cells, column widths and cell shading kept).
* Pictures become pictures.
* A single empty paragraph between sections is kept as a blank line; runs of
  several blank lines are collapsed to one.
* Headers, footers, footnotes, comments and tables of contents are left out.

Word documents have no pages in the file, so everything is "page 1".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..model import Block, Document, Image, Run, Table, TableCell, normalise_whitespace
from ..numbering import int_to_roman
from .base import ParseError
from .images import to_png
from .pdf.structure import strip_uniform_style

try:  # python-docx is only needed for Word files
    import docx
    from docx.oxml.ns import qn
    from docx.text.hyperlink import Hyperlink
    from docx.text.paragraph import Paragraph
except ImportError:  # pragma: no cover
    docx = None

EMU_PER_PT = 12700

# Word's highlight colour names -> hex
HIGHLIGHT_HEX = {
    "yellow": "#FFFF00", "brightGreen": "#00FF00", "turquoise": "#00FFFF", "pink": "#FF00FF",
    "blue": "#0000FF", "red": "#FF0000", "darkBlue": "#000080", "teal": "#008080",
    "green": "#008000", "violet": "#800080", "darkRed": "#800000", "darkYellow": "#808000",
    "darkGray": "#808080", "lightGray": "#C0C0C0", "black": "#000000",
}

_LIST_STYLE_LEVEL = re.compile(r"^List (?:Bullet|Number|Continue) (\d)$")
_TOC_STYLE = re.compile(r"^(toc|TOC) ?\d|^TOC Heading$|^Table of (Contents|Figures)", re.I)


def _w(el, tag):
    return el.find(qn(tag)) if el is not None else None


def _val(el, attr="w:val"):
    return el.get(qn(attr)) if el is not None else None


# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #

def _style_chain(style):
    """The style and the styles it's based on, nearest first."""
    seen = 0
    while style is not None and seen < 20:
        yield style
        style = style.base_style
        seen += 1


def _outline_level(paragraph) -> int | None:
    """0-8 for heading levels 1-9; None for body text."""
    pPr = paragraph._p.pPr
    v = _val(_w(pPr, "w:outlineLvl"))
    if v is None:
        for st in _style_chain(paragraph.style):
            v = _val(_w(st.element.pPr, "w:outlineLvl"))
            if v is not None:
                break
    if v is None:
        return None
    lvl = int(v)
    return lvl if lvl < 9 else None  # 9 = "body text"


def _style_font_attr(style, attr):
    for st in _style_chain(style):
        value = getattr(st.font, attr, None)
        if value is not None:
            return value
    return None


# --------------------------------------------------------------------------- #
# Numbering (Word's automatic list numbers and bullets)
# --------------------------------------------------------------------------- #

@dataclass
class _Level:
    fmt: str        # "bullet", "decimal", "lowerLetter", "lowerRoman", ...
    text: str       # e.g. "%1." or "(%2)"
    start: int


class _Numbering:
    """Works out the bullet/number Word shows for each list paragraph."""

    def __init__(self, document):
        self.levels: dict[tuple[str, int], _Level] = {}
        self.num_to_abstract: dict[str, str] = {}
        self.overrides: dict[tuple[str, int], int] = {}
        self.counters: dict[str, dict[int, int]] = {}
        self.started: set[str] = set()
        try:
            root = document.part.numbering_part.element
        except Exception:
            return
        for an in root.findall(qn("w:abstractNum")):
            aid = an.get(qn("w:abstractNumId"))
            for lvl in an.findall(qn("w:lvl")):
                self.levels[(aid, int(lvl.get(qn("w:ilvl"))))] = _Level(
                    _val(_w(lvl, "w:numFmt")) or "decimal",
                    _val(_w(lvl, "w:lvlText")) or "",
                    int(_val(_w(lvl, "w:start")) or 1),
                )
        for num in root.findall(qn("w:num")):
            nid = num.get(qn("w:numId"))
            self.num_to_abstract[nid] = _val(_w(num, "w:abstractNumId"))
            for ov in num.findall(qn("w:lvlOverride")):
                start = _val(_w(ov, "w:startOverride"))
                if start is not None:
                    self.overrides[(nid, int(ov.get(qn("w:ilvl"))))] = int(start)

    def list_info(self, paragraph) -> tuple[str, int] | None:
        """(numId, level) for a list paragraph, from the paragraph or its style."""
        numPr = paragraph._p.pPr.numPr if paragraph._p.pPr is not None else None
        nid = ilvl = None
        if numPr is not None:
            nid = _val(numPr.numId) if numPr.numId is not None else None
            ilvl = numPr.ilvl.val if numPr.ilvl is not None else None
        if nid is None:
            for st in _style_chain(paragraph.style):
                pPr = st.element.pPr
                sp = pPr.numPr if pPr is not None else None
                if sp is not None and sp.numId is not None:
                    nid = str(sp.numId.val)
                    if ilvl is None and sp.ilvl is not None:
                        ilvl = sp.ilvl.val
                    break
        if nid is None or nid == "0":
            return None
        level = int(ilvl or 0)
        m = _LIST_STYLE_LEVEL.match(paragraph.style.name or "")
        if level == 0 and m:  # "List Bullet 2" etc. are level 2 by name
            level = int(m.group(1)) - 1
        return str(nid), level

    def level_def(self, nid: str, level: int) -> _Level | None:
        aid = self.num_to_abstract.get(nid)
        return self.levels.get((aid, level)) if aid is not None else None

    def next_label(self, nid: str, level: int) -> str:
        """Advance this list's counter and return the displayed number text."""
        aid = self.num_to_abstract.get(nid, nid)
        counters = self.counters.setdefault(aid, {})
        if nid not in self.started:
            self.started.add(nid)
            for (onid, olvl), start in self.overrides.items():
                if onid == nid:
                    counters[olvl] = start - 1
        lvl = self.level_def(nid, level)
        start = lvl.start if lvl else 1
        counters[level] = counters.get(level, start - 1) + 1
        for deeper in [k for k in counters if k > level]:  # restart sub-levels
            del counters[deeper]
        text = lvl.text if lvl else "%1."

        def repl(m):
            lv = int(m.group(1)) - 1
            d = self.level_def(nid, lv)
            n = counters.get(lv, d.start if d else 1)
            return _format_number(n, d.fmt if d else "decimal")

        return re.sub(r"%(\d)", repl, text)


def _format_number(n: int, fmt: str) -> str:
    if fmt in ("lowerLetter", "upperLetter"):
        s = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(97 + r) + s
        return s.upper() if fmt == "upperLetter" else s
    if fmt == "lowerRoman":
        return int_to_roman(n)
    if fmt == "upperRoman":
        return int_to_roman(n).upper()
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt == "none":
        return ""
    return str(n)


# --------------------------------------------------------------------------- #
# The parser
# --------------------------------------------------------------------------- #

class DocxParser:
    extensions = (".docx",)

    def parse(self, path: Path, settings: Settings) -> Document:
        if docx is None:
            raise ParseError("Word support isn't installed (python-docx).")
        try:
            document = docx.Document(str(path))
        except Exception as exc:
            raise ParseError(f"Couldn't open this Word file: {exc}") from exc
        self.document = document
        self.numbering = _Numbering(document)
        self.warnings: list[str] = []

        blocks: list[Block] = []
        for el in self._body_elements(document.element.body):
            if el.tag == qn("w:p"):
                blocks.extend(self._paragraph(Paragraph(el, document.part), in_table=False))
            elif el.tag == qn("w:tbl"):
                blocks.extend(self._table(el))
        blocks = _tidy_spacers(blocks)
        if not any(not b.spacer for b in blocks):
            raise ParseError("This Word document doesn't seem to contain any text.")
        return Document(blocks, str(path), layout="document", page_count=1, warnings=self.warnings)

    # ------------------------------------------------------------------ #
    def _body_elements(self, parent):
        """Paragraphs and tables in order, looking inside content controls but
        skipping tables of contents."""
        for el in parent:
            if el.tag in (qn("w:p"), qn("w:tbl")):
                yield el
            elif el.tag == qn("w:sdt"):
                gallery = el.find(".//" + qn("w:docPartGallery"))
                if gallery is not None and "Contents" in (_val(gallery) or ""):
                    continue
                content = el.find(qn("w:sdtContent"))
                if content is not None:
                    yield from self._body_elements(content)

    # ------------------------------------------------------------------ #
    def _paragraph(self, p, in_table: bool) -> list[Block]:
        style_name = p.style.name if p.style is not None else ""
        if _TOC_STYLE.search(style_name or ""):
            return []
        runs, images = self._runs(p)
        text = "".join(r.text for r in runs).strip()
        image_blocks = [Block("image", image=img, note="picture from the Word document") for img in images]

        if not text:
            return image_blocks or [Block("paragraph", spacer=True, note="blank line")]

        outline = None if in_table else _outline_level(p)
        info = self.numbering.list_info(p)
        label = ""
        kind, level, style_key, note = "paragraph", 0, None, ""

        if info is not None:
            nid, lvl = info
            ldef = self.numbering.level_def(nid, lvl)
            if ldef is None or ldef.fmt == "bullet":
                kind, level = "bullet", lvl
                note = f"Word bullet, level {lvl + 1}"
            else:
                label = self.numbering.next_label(nid, lvl)
                level = lvl + 1  # numbered paragraphs are indented like list items
                note = f"Word numbered list item {label!r}"

        if style_name == "Title" and not in_table:
            block = Block("paragraph", runs, page=1, selected=False, note="document title (unticked by default)")
        elif outline is not None:
            # Headings: Word's own level, numbering (if any) kept as text
            if label:
                runs = [Run(label + " ")] + runs
            block = Block("heading", strip_uniform_style(runs), style_key=f"word_h{min(outline + 1, 6)}",
                          label=label or None, note=f"Word heading level {outline + 1} ({style_name})")
        else:
            if label:
                runs = [Run(label + " ")] + runs
            block = Block(kind, runs, level=level, note=note)

        # Line breaks inside a paragraph (Shift+Enter) start a new line in the
        # Google Doc too. Later lines of a bullet are indented under its text.
        out = []
        for i, piece in enumerate(_split_lines(block.runs)):
            if i == 0:
                b = block
                b.runs = piece
            else:
                b = Block("paragraph" if block.kind != "heading" else "heading", piece,
                          level=(block.level + 1 if block.kind == "bullet" else block.level),
                          style_key=block.style_key, note="line break in the Word paragraph")
            b.runs = normalise_whitespace(b.runs)
            if b.runs:
                out.append(b)
        return out + image_blocks

    def _runs(self, p) -> tuple[list[Run], list[Image]]:
        runs: list[Run] = []
        images: list[Image] = []
        for item in p.iter_inner_content():
            inner = item.runs if isinstance(item, Hyperlink) else [item]
            for r in inner:
                images.extend(self._images(r))
                text = r.text
                if not text:
                    continue
                runs.append(Run(
                    text,
                    bold=bool(self._eff(r, p, "bold")),
                    italic=bool(self._eff(r, p, "italic")),
                    underline=bool(self._eff(r, p, "underline")),
                    superscript=bool(r.font.superscript),
                    subscript=bool(r.font.subscript),
                    color=_run_colour(r),
                    highlight=_run_highlight(r),
                ))
        return runs, images

    @staticmethod
    def _eff(run, paragraph, attr):
        """Effective on/off formatting: the run's own setting, else its
        character style's, else the paragraph style's."""
        value = getattr(run.font, attr)
        if value is not None:
            return value not in (False,) and str(value) != "NONE (0)"
        if run.style is not None:
            value = _style_font_attr(run.style, attr)
            if value is not None:
                return value
        return _style_font_attr(paragraph.style, attr) if paragraph.style is not None else None

    def _images(self, run) -> list[Image]:
        out = []
        # Old-style embedded objects (e.g. Equation Editor) show a preview picture
        for imagedata in run._r.iter("{urn:schemas-microsoft-com:vml}imagedata"):
            rid = imagedata.get(qn("r:id"))
            part = self.document.part.related_parts.get(rid)
            png = to_png(part.blob) if part is not None else None
            shape = imagedata.getparent()
            w, h = _vml_size(shape.get("style", "") if shape is not None else "")
            if png:
                out.append(Image(png, w, h, "Equation or embedded object from the Word document"))
        for drawing in run._r.findall(".//" + qn("w:drawing")):
            blip = drawing.find(".//" + qn("a:blip"))
            extent = drawing.find(".//" + qn("wp:extent"))
            if blip is None:
                continue
            rid = blip.get(qn("r:embed"))
            part = self.document.part.related_parts.get(rid)
            png = to_png(part.blob) if part is not None else None
            if png is None:
                self.warnings.append("A picture in a format Google Docs can't use (e.g. EMF) was skipped.")
                continue
            w = int(extent.get("cx")) / EMU_PER_PT if extent is not None else 300
            h = int(extent.get("cy")) / EMU_PER_PT if extent is not None else 200
            out.append(Image(png, w, h, "Picture from the Word document"))
        return out

    # ------------------------------------------------------------------ #
    def _table(self, tbl) -> list[Block]:
        """The table, followed by any pictures that were inside its cells
        (pictures can't go inside the Google Doc table, so they come right
        after it)."""
        grid = [int(g.get(qn("w:w")) or 0) for g in tbl.findall(qn("w:tblGrid") + "/" + qn("w:gridCol"))]
        rows = tbl.findall(qn("w:tr"))
        cells: list[TableCell] = []
        lifted: list[Block] = []  # pictures found inside cells
        open_cells: dict[int, TableCell] = {}  # column -> cell that may continue downwards
        n_cols = len(grid)
        for r, tr in enumerate(rows):
            col = 0
            for tc in tr.findall(qn("w:tc")):
                tcPr = tc.tcPr
                span = int(_val(_w(tcPr, "w:gridSpan")) or 1)
                vmerge = _w(tcPr, "w:vMerge")
                if vmerge is not None and _val(vmerge) != "restart" and col in open_cells:
                    open_cells[col].rowspan += 1   # continuation of the cell above
                    col += span
                    continue
                blocks: list[Block] = []
                # .iter() also reaches paragraphs of tables nested in this cell;
                # their text is kept, in order, as the cell's paragraphs
                for el in tc.iter(qn("w:p")):
                    blocks.extend(self._paragraph(Paragraph(el, self.document.part), in_table=True))
                lifted.extend(b for b in blocks if b.kind == "image")
                blocks = [b for b in blocks if b.kind != "image"]
                cell = TableCell(r, col, _tidy_spacers(blocks), colspan=span,
                                 background=_cell_shading(tcPr))
                cells.append(cell)
                open_cells[col] = cell
                col += span
            n_cols = max(n_cols, col)
        table = Table(len(rows), n_cols, cells, col_widths=[float(w) for w in grid] if len(grid) == n_cols else [])
        for img in lifted:
            img.note = "picture from inside a table (placed after it)"
        return [Block("table", table=table, note=f"Word table {len(rows)} rows x {n_cols} columns")] + lifted


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _vml_size(style: str) -> tuple[float, float]:
    """Width/height in points from a VML style like "width:96pt;height:18pt"."""
    found = dict(re.findall(r"(width|height):([\d.]+)pt", style))
    return float(found.get("width", 120)), float(found.get("height", 30))


def _run_colour(run) -> str | None:
    """Only colour applied directly to the text; theme/automatic colours are
    the document's look, not meaning."""
    try:
        c = run.font.color
        if c is None or c.type is None or c.rgb is None:
            return None
        hex_ = f"#{c.rgb}".upper()
    except Exception:
        return None
    r, g, b = int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16)
    if max(r, g, b) < 0x30:  # black-ish = normal text
        return None
    return hex_


def _run_highlight(run) -> str | None:
    rPr = run._r.rPr
    hl = _val(_w(rPr, "w:highlight"))
    if hl and hl != "none":
        return HIGHLIGHT_HEX.get(hl)
    shd = _w(rPr, "w:shd")
    fill = shd.get(qn("w:fill")) if shd is not None else None
    if fill and fill.lower() not in ("auto", "ffffff"):
        return f"#{fill.upper()}"
    return None


def _cell_shading(tcPr) -> str | None:
    shd = _w(tcPr, "w:shd")
    fill = shd.get(qn("w:fill")) if shd is not None else None
    if fill and fill.lower() not in ("auto", "ffffff"):
        return f"#{fill.upper()}"
    return None


def _split_lines(runs: list[Run]) -> list[list[Run]]:
    """Split runs at line breaks ("\\n" from Shift+Enter)."""
    pieces: list[list[Run]] = [[]]
    for r in runs:
        parts = r.text.replace("\r", "\n").split("\n")
        for i, part in enumerate(parts):
            if i:
                pieces.append([])
            if part:
                pieces[-1].append(Run(part, r.bold, r.italic, r.underline, r.superscript, r.subscript,
                                      r.color, r.highlight))
    return [p for p in pieces if "".join(r.text for r in p).strip()]


def _tidy_spacers(blocks: list[Block]) -> list[Block]:
    """Keep single blank lines between content; drop them at the start/end,
    next to headings, tables and pictures (those already get spacing)."""
    out: list[Block] = []
    for b in blocks:
        if b.spacer:
            if not out or out[-1].spacer or out[-1].kind in ("heading", "table", "image"):
                continue
        elif b.kind in ("heading", "table", "image") and out and out[-1].spacer:
            out.pop()
        out.append(b)
    while out and out[-1].spacer:
        out.pop()
    return out
