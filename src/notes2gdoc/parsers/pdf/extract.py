"""Turn a PDF page into a list of visual `Line`s with styled `Piece`s.

PyMuPDF reports text as blocks > lines > spans > characters. Its "lines" aren't
always what a reader would call a line:
* Word often emits a heading number and its text as two separate lines on the
  same baseline ("1." and "LIQUIDATION"; "(a)" and "Overview...").
* PowerPoint's justified text puts each word in its own line.
So we regroup everything by vertical position into visual lines, and insert a
space wherever there's a visible horizontal gap between two spans.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Union

import pymupdf as fitz

from ...model import Run
from .layout import Box, Segment, analyse_drawings
from .regions import DiagramRegion, TableRegion, find_diagrams, find_pictures, find_ruled_tables, find_tables

# Fonts whose characters are pictures, not letters. A line starting with one of
# their characters (e.g. Wingdings "Ø", which displays as an arrow) is a bullet.
_SYMBOL_FONT = re.compile(r"symbol|wingding|webding|dingbat|zapf", re.I)

# Characters treated as bullet glyphs when they start a line. The \uf0xx
# entries are Symbol/Wingdings private-use code points some PDFs emit instead
# of real Unicode bullets.
BULLET_GLYPHS = set("•●○◦▪■□▫◆◇♦❖➢➤►▶‣⁃∙·✓✔➔→") | {
    "", "", "", "", "", "", "", "",
}
# Dashes count as bullets only if followed by a space (so "-5" or "–2101]" don't).
DASH_BULLETS = set("-–—")

_BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demibold", re.I)
_ITALIC_FONT = re.compile(r"italic|oblique", re.I)

# Extraction flags: keep whitespace, clip to page, and *don't* preserve
# ligatures, so "ﬁ" comes out as "fi".
TEXT_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP


@dataclass
class Piece:
    """A run of characters with one style, plus its horizontal extent."""

    text: str
    bold: bool
    italic: bool
    underline: bool
    superscript: bool
    subscript: bool
    color: str | None
    size: float
    x0: float
    x1: float
    vis_x0: float | None = None  # x of the first non-space character

    def style(self) -> tuple:
        return (self.bold, self.italic, self.underline, self.superscript, self.subscript, self.color)


@dataclass
class Line:
    page: int                     # 0-based page index
    x0: float                     # left edge of the first visible character (the glyph, for bullets)
    y0: float
    x1: float
    y1: float
    size: float                   # dominant font size
    pieces: list[Piece] = field(default_factory=list)
    bullet: str | None = None     # bullet glyph, already removed from `pieces`
    text_x0: float = 0.0          # left edge of the text (after any bullet glyph)
    in_box: bool = False
    blank: bool = False           # whitespace-only line (a deliberate empty paragraph)
    # Pieces before the bullet glyph was stripped. A line starting "– 2101]" may
    # be a wrapped continuation rather than a dash bullet; structure.py decides
    # and restores these if so.
    pieces_with_glyph: list[Piece] = field(default_factory=list)
    # x used to work out bullet nesting. Same as x0, except in the right-hand
    # column of a two-column page, where it's shifted to line up with the left.
    level_x: float = 0.0
    # 0 = normal full-width text; 1/2 = left/right column of a two-column page
    column: int = 0
    # x of the first letter of each word (for "where does the text after the
    # list marker start?")
    word_starts: list[float] = field(default_factory=list)
    page_width: float = 0.0
    page_height: float = 0.0
    # Inside a process banner (a row of chevrons/boxes such as "1. Documents >
    # 2. Issue Certificate > 3. Registration"): the step this slide is about
    banner: bool = False

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.pieces)

    @property
    def bold_ratio(self) -> float:
        total = sum(len(p.text.strip()) for p in self.pieces)
        if not total:
            return 0.0
        return sum(len(p.text.strip()) for p in self.pieces if p.bold) / total

    def runs(self) -> list[Run]:
        return [
            Run(p.text, p.bold, p.italic, p.underline, p.superscript, p.subscript, p.color)
            for p in self.pieces
        ]


def colour_hex(value: int) -> str | None:
    """PyMuPDF sRGB int -> "#RRGGBB".

    Near-black is treated as "no colour" (the doc's normal text colour).
    Near-white is too: it's text that sat on a dark box in a slide, and would be
    invisible on a white Google Doc page.
    """
    r, g, b = (value >> 16) & 255, (value >> 8) & 255, value & 255
    if max(r, g, b) < 0x30 or min(r, g, b) > 0xE6:
        return None
    # Dark grey (e.g. #404040) is a common "body text" colour in templates
    if max(r, g, b) < 0x50 and max(r, g, b) - min(r, g, b) < 0x10:
        return None
    return f"#{r:02X}{g:02X}{b:02X}"


@dataclass
class _Char:
    c: str
    x0: float
    x1: float
    origin_y: float


@dataclass
class _Span:
    chars: list[_Char]
    size: float
    font: str
    flags: int
    color: int
    x0: float
    y0: float
    x1: float
    y1: float


PageItem = Union[Line, TableRegion, DiagramRegion]

# Two-column layouts: the empty vertical strip between columns must be at least
# this wide (points) and lie in the middle half of the page.
MIN_GUTTER = 18


def extract_page(page: fitz.Page, page_index: int, repeated_images: set[int] | None = None) -> list[PageItem]:
    """Return the page's content in reading order: Lines, plus TableRegion and
    DiagramRegion objects (whose own text is kept inside them)."""
    drawings = page.get_drawings()
    boxes, underline_segs = analyse_drawings(page, drawings)
    # Slide/page numbers go first: otherwise one sitting at the same height as
    # the last line of text gets glued onto it ("…not just to LPAs 32").
    raw_lines = [sp for sp in (_drop_number_span(sp, page) for sp in _raw_lines(page))
                 if sp and not _page_number(sp, page)]

    # Process banners: the steps that aren't the current one are written in
    # near-white, barely visible on the light boxes. Leave those out.
    banners = _banner_boxes(drawings)
    raw_lines = [sp for sp in (_drop_faint(sp, banners) for sp in raw_lines) if sp]

    visible = [sp for sp in raw_lines if _visible(sp)]
    tables = find_tables(page, drawings)
    tables += find_ruled_tables(page, drawings,
                                _table_texts([sp for sp in visible if not _page_number(sp, page)]),
                                [t.rect for t in tables])
    diagrams = find_diagrams(page, drawings, [_rect(sp) for sp in visible], [t.rect for t in tables])
    diagrams += find_pictures(page, repeated_images or set(), [r.rect for r in [*tables, *diagrams]],
                              [_rect(sp) for sp in visible])

    def build(span_lists: list[list[_Span]]) -> list[Line]:
        lines = [_build_line(g, page, page_index, boxes, underline_segs) for g in _group_visual(span_lists)]
        return sorted(lines, key=lambda ln: (round(ln.y0, 1), ln.x0))

    # 1. Route each PyMuPDF line to a table cell, a diagram, or the normal flow.
    flow: list[list[_Span]] = []
    for sp in raw_lines:
        r = _rect(sp)
        centre = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        table = None if _page_number(sp, page) else next((t for t in tables if t.rect.contains(centre)), None)
        if table:
            for part in _split_at(sp, table.splits):
                pr = _rect(part)
                pc = fitz.Point((pr.x0 + pr.x1) / 2, (pr.y0 + pr.y1) / 2)
                cell = next((c for c in table.cells if c.rect.contains(pc)), None)
                if cell:
                    cell.lines.append(part)
            continue
        diagram = next((d for d in diagrams if d.rect.contains(centre)), None)
        if diagram:
            diagram.lines.append(sp)
            continue
        flow.append(sp)

    for t in tables:
        for c in t.cells:
            c.lines = [ln for ln in build(c.lines) if not ln.blank]
    for d in diagrams:
        _build_diagram_text(d, page, page_index, boxes)

    # 2. Columns: read the left column top to bottom, then the right one.
    gutter = _find_gutter(flow, page)
    items: list[tuple[int, float, PageItem]] = []
    if gutter is None:
        for ln in build(flow):
            items.append((0, ln.y0, ln))
        for region in [*tables, *diagrams]:
            items.append((0, region.y0, region))
    else:
        left = [sp for sp in flow if _rect(sp).x1 <= gutter]
        right = [sp for sp in flow if _rect(sp).x0 >= gutter]
        full = [sp for sp in flow if _rect(sp).x1 > gutter and _rect(sp).x0 < gutter]
        left_lines, right_lines = build(left), build(right)
        col_top = min(ln.y0 for ln in left_lines + right_lines)
        left_edge = min(ln.x0 for ln in left_lines)
        right_edge = min(ln.x0 for ln in right_lines)
        for ln in left_lines:
            ln.column = 1
            items.append((1, ln.y0, ln))
        for ln in right_lines:
            ln.column = 2
            # Measure bullet indents from the column's own left edge, so the
            # right column's bullets get the same levels as the left column's.
            ln.level_x = ln.x0 - right_edge + left_edge
            items.append((2, ln.y0, ln))
        for ln in build(full):
            items.append((0 if ln.y0 < col_top else 3, ln.y0, ln))
        for region in [*tables, *diagrams]:
            items.append((0 if region.y0 < col_top else 3, region.y0, region))

    for _, _, it in items:
        if isinstance(it, Line) and banners:
            c = fitz.Point((it.x0 + it.x1) / 2, (it.y0 + it.y1) / 2)
            it.banner = any(r.contains(c) for r in banners)
    items.sort(key=lambda it: (it[0], it[1]))
    return [it[2] for it in items]


def _banner_boxes(drawings: list[dict]) -> list[fitz.Rect]:
    """Boxes forming a process banner: 3+ light filled shapes of the same
    height, side by side (touching or overlapping, like chevrons). Dark boxes
    with white text, like a table's header row, aren't banners."""
    def looks_light(d) -> bool:
        # The colour you see: the fill blended with the white page by its
        # opacity (the chevrons are black at 10% opacity = light grey)
        alpha = d.get("fill_opacity") if d.get("fill_opacity") is not None else 1.0
        lum = sum(d["fill"]) / 3
        return 1 - alpha * (1 - lum) > 0.7

    boxes = sorted((fitz.Rect(d["rect"]) for d in drawings
                    if d.get("fill") is not None and looks_light(d) and 50 <= d["rect"].width <= 400
                    and 15 <= d["rect"].height <= 150), key=lambda r: r.x0)
    out: list[fitz.Rect] = []
    used: set[int] = set()
    for i, r in enumerate(boxes):
        if i in used:
            continue
        row = [r]
        for j in range(i + 1, len(boxes)):
            o = boxes[j]
            if (abs(o.y0 - r.y0) <= 4 and abs(o.y1 - r.y1) <= 4
                    and o.x0 - row[-1].x1 <= 30 and o.x0 > row[-1].x0):
                row.append(o)
                used.add(j)
        if len(row) >= 3:
            out.extend(row)
    return out


def _drop_faint(spans: list[_Span], banners: list[fitz.Rect]) -> list[_Span]:
    """Remove near-white text sitting in a process banner box."""
    if not banners:
        return spans
    keep = []
    for s in spans:
        r, g, b = (s.color >> 16) & 255, (s.color >> 8) & 255, s.color & 255
        c = fitz.Point((s.x0 + s.x1) / 2, (s.y0 + s.y1) / 2)
        if min(r, g, b) >= 0xE0 and any(box.contains(c) for box in banners):
            continue
        keep.append(s)
    return keep


def _build_diagram_text(d: DiagramRegion, page, page_index, boxes) -> None:
    """Turn a diagram's raw text into Lines, box by box.

    Text is assigned to its box *before* joining same-height fragments into
    lines, so two boxes side by side ("Identify the target" | "Pre-Acquisition")
    don't get glued together. Text outside any box (arrow labels) is kept one
    PyMuPDF line at a time. Underline detection is off here, because connector
    lines running under labels would look like underlines.
    """
    per_shape: list[list[list[_Span]]] = [[] for _ in d.shapes]
    loose: list[list[_Span]] = []
    for sp in d.lines:
        r = _rect(sp)
        centre = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        idx = next((i for i, s in enumerate(d.shapes) if s.contains(centre)), None)
        (per_shape[idx] if idx is not None else loose).append(sp)

    def make(group: list[list[_Span]]) -> Line:
        return _build_line(group, page, page_index, boxes, [])

    groups: list[tuple[fitz.Rect, list[Line]]] = []
    for shape, raw in zip(d.shapes, per_shape):
        lines = [make(g) for g in _group_visual(raw)]
        lines = sorted((ln for ln in lines if not ln.blank), key=lambda ln: (round(ln.y0, 1), ln.x0))
        if lines:
            groups.append((shape, lines))
    d.groups = groups
    d.labels = [ln for ln in (make(sp) for sp in loose) if not ln.blank]
    d.lines = [ln for _, g in groups for ln in g] + d.labels


def _raw_lines(page: fitz.Page) -> list[list[_Span]]:
    """PyMuPDF lines as lists of spans (with per-character positions)."""
    raw = page.get_text("rawdict", flags=TEXT_FLAGS)
    out: list[list[_Span]] = []
    for block in raw["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            spans = []
            for s in line["spans"]:
                chars = [
                    _Char(ch["c"], ch["bbox"][0], ch["bbox"][2], ch["origin"][1])
                    for ch in s["chars"]
                ]
                if chars:
                    spans.append(_Span(chars, s["size"], s["font"], s["flags"], s["color"], *s["bbox"]))
            out.extend(_split_baselines(spans))
    return out


def _split_baselines(spans: list[_Span]) -> list[list[_Span]]:
    """PyMuPDF sometimes puts text from two different lines into one line when
    they overlap vertically, e.g. the last word of a left-column line and a
    slightly lower right-column heading. Split where the baseline jumps
    between text of the same size (a superscript is smaller, so stays)."""
    parts: list[list[_Span]] = []
    for s in spans:
        if parts:
            prev = parts[-1][-1]
            jump = abs(s.chars[0].origin_y - prev.chars[0].origin_y)
            same_size = abs(s.size - prev.size) <= 0.15 * max(s.size, prev.size)
            if same_size and jump > 0.3 * max(s.size, prev.size):
                parts.append([s])
                continue
            parts[-1].append(s)
        else:
            parts.append([s])
    return [p for p in parts if p]


def _page_number(spans: list[_Span], page: fitz.Page) -> bool:
    """A lone number in the bottom tenth of the page (a slide/page number).
    It stays in the normal flow, where furniture.py removes it, even when it
    sits inside a table's area."""
    text = "".join(ch.c for s in spans for ch in s.chars).strip()
    return text.isdigit() and len(text) <= 3 and _rect(spans).y0 > page.rect.height * 0.9


def _words(spans: list[_Span]) -> list[tuple[float, float]]:
    """(x0, x1) of each word on a raw line."""
    out: list[list[float]] = []
    prev_x1: float | None = None
    for s in spans:
        for ch in s.chars:
            if ch.c.isspace():
                prev_x1 = None
                continue
            if prev_x1 is None or ch.x0 - prev_x1 > 0.25 * s.size:
                out.append([ch.x0, ch.x1])
            else:
                out[-1][1] = ch.x1
            prev_x1 = ch.x1
    return [(a, b) for a, b in out]


_LONE_MARKER = re.compile(r"^\(?(\d{1,3}|[a-zA-Z]|[ivxIVX]{1,4})[.)]$")


def _table_texts(lines: list[list[_Span]]) -> list[tuple[fitz.Rect, list[float], list[tuple[float, float]]]]:
    """(box, style breaks, words) per text line, for finding ruled tables. A bullet
    glyph that PyMuPDF keeps as its own line ("•" ... "Keep accounts") is
    joined to the text beside it, so the gap between them doesn't look like
    a column edge."""
    rects = [(_rect(sp), _style_breaks(sp), "".join(ch.c for s in sp for ch in s.chars).strip(), _words(sp))
             for sp in lines]

    def is_glyph(text: str) -> bool:  # a bullet, or a list number like "10."
        return text in BULLET_GLYPHS or bool(_LONE_MARKER.match(text))

    out = []
    for r, brk, text, words in rects:
        if is_glyph(text):
            mate = next((o for o in rects if o[2] != text and 0 < o[0].x0 - r.x1 <= 40
                         and min(o[0].y1, r.y1) - max(o[0].y0, r.y0) > 0.5 * r.height), None)
            if mate is not None:
                continue  # covered by its text's box below
        else:
            glyph = next((o for o in rects if is_glyph(o[2]) and 0 < r.x0 - o[0].x1 <= 40
                          and min(o[0].y1, r.y1) - max(o[0].y0, r.y0) > 0.5 * r.height), None)
            if glyph is not None:
                r = fitz.Rect(glyph[0].x0, r.y0, r.x1, r.y1)
                words = glyph[3] + words
        out.append((r, brk, words))
    return out


def _drop_number_span(spans: list[_Span], page: fitz.Page) -> list[_Span]:
    """Remove a slide number that the PDF put in the same line as some text:
    a short run of digits at the bottom of the page, far to the right of the
    text or in a much smaller font."""
    if len(spans) < 2:
        return spans
    last = spans[-1]
    digits = "".join(ch.c for ch in last.chars).strip()
    if not (digits.isdigit() and len(digits) <= 3 and last.y0 > page.rect.height * 0.9):
        return spans
    rest = spans[:-1]
    gap = last.x0 - max(s.x1 for s in rest)
    if gap > 3 * last.size or last.size < 0.75 * max(s.size for s in rest):
        return rest
    return spans


def _style_breaks(spans: list[_Span]) -> list[float]:
    """x of each word on a raw line that starts in a different style (font,
    colour) from the text before it, e.g. "Assume" in a line reading
    "**Section 3(2)** Assume person has capacity". In a ruled table, that's
    where a label ends and its text begins."""
    out: list[float] = []
    prev_style = None
    for s in spans:
        style = (s.font, s.flags, s.color)
        visible = [ch for ch in s.chars if not ch.c.isspace()]
        if not visible:
            continue
        if prev_style is not None and style != prev_style:
            out.append(visible[0].x0)
        prev_style = style
    return out


def _split_at(spans: list[_Span], xs: list[float]) -> list[list[_Span]]:
    """Cut a raw line where its style changes right at one of the x positions
    (a ruled table's column edges). Other lines come back whole."""
    if not xs or not _visible(spans):
        return [spans]
    r = _rect(spans)
    starts = _style_breaks(spans)
    cuts = [x for x in xs if r.x0 + 1 < x < r.x1 and any(abs(w - x) <= 4 for w in starts)]
    if not cuts:
        return [spans]
    edges = [-1e9, *[x - 4 for x in cuts], 1e9]
    parts: list[list[_Span]] = []
    for lo, hi in zip(edges, edges[1:]):
        part = []
        for s in spans:
            chars = [ch for ch in s.chars if lo <= ch.x0 < hi]
            if chars:
                part.append(_Span(chars, s.size, s.font, s.flags, s.color,
                                  chars[0].x0, s.y0, chars[-1].x1, s.y1))
        if part and _visible(part):
            parts.append(part)
    return parts or [spans]


def _visible(spans: list[_Span]) -> bool:
    return any(not ch.c.isspace() for s in spans for ch in s.chars)


def _rect(spans: list[_Span]) -> fitz.Rect:
    """Bounding box of the visible characters (falls back to span boxes)."""
    chars = [(ch, s) for s in spans for ch in s.chars if not ch.c.isspace()]
    if chars:
        return fitz.Rect(
            min(ch.x0 for ch, _ in chars), min(s.y0 for _, s in chars),
            max(ch.x1 for ch, _ in chars), max(s.y1 for _, s in chars),
        )
    return fitz.Rect(min(s.x0 for s in spans), min(s.y0 for s in spans),
                     max(s.x1 for s in spans), max(s.y1 for s in spans))


def _find_gutter(flow: list[list[_Span]], page: fitz.Page) -> float | None:
    """Look for an empty vertical strip that splits the body into two columns.

    Only text below the title area and above the footer counts. Every text line
    must stay on one side of the strip; if any line crosses it, there are no
    columns. Returns the strip's x-midpoint, or None.
    """
    w, h = page.rect.width, page.rect.height
    rects = [_rect(sp) for sp in flow if _visible(sp)]
    rects = [r for r in rects if 0.18 * h <= r.y0 <= 0.9 * h]
    if len(rects) < 4:
        return None
    spans = sorted((r.x0, r.x1) for r in rects)
    merged: list[list[float]] = []
    for x0, x1 in spans:
        if merged and x0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])
    best = None
    for (_, a1), (b0, _) in zip(merged, merged[1:]):
        mid = (a1 + b0) / 2
        if b0 - a1 >= MIN_GUTTER and 0.25 * w <= mid <= 0.75 * w:
            if best is None or b0 - a1 > best[1]:
                best = (mid, b0 - a1)
    if best is None:
        return None
    mid = best[0]
    left = [r for r in rects if r.x1 <= mid]
    right = [r for r in rects if r.x0 >= mid]
    if len(left) < 2 or len(right) < 2:
        return None
    # The columns must sit side by side, not one above the other
    overlap = min(max(r.y1 for r in left), max(r.y1 for r in right)) - max(
        min(r.y0 for r in left), min(r.y0 for r in right))
    return mid if overlap > 0 else None


def _group_visual(raw_lines: list[list[_Span]]) -> list[list[_Span]]:
    """Group PyMuPDF lines into visual lines: lines whose vertical extents
    mostly overlap belong together (handles "1." + "LIQUIDATION", and
    justified text exported as one PyMuPDF line per word)."""
    groups: list[list[_Span]] = []
    for spans in sorted(raw_lines, key=lambda sp: min(s.y0 for s in sp)):
        y0 = min(s.y0 for s in spans)
        y1 = max(s.y1 for s in spans)
        placed = False
        for g in groups:
            gy0 = min(s.y0 for s in g)
            gy1 = max(s.y1 for s in g)
            overlap = min(y1, gy1) - max(y0, gy0)
            if overlap > 0.5 * min(y1 - y0, gy1 - gy0):
                g.extend(spans)
                placed = True
                break
        if not placed:
            groups.append(list(spans))
    return groups


def _build_line(spans, page, page_index, boxes: list[Box], segs: list[Segment]) -> Line:
    spans = sorted(spans, key=lambda s: s.x0)

    # Dominant size and baseline, weighted by visible characters
    weights: dict[float, int] = {}
    for s in spans:
        n = sum(1 for ch in s.chars if not ch.c.isspace())
        weights[round(s.size, 1)] = weights.get(round(s.size, 1), 0) + n
    dom_size = max(weights, key=weights.get) if any(weights.values()) else spans[0].size
    base_spans = [s for s in spans if abs(s.size - dom_size) < 0.6] or spans
    origins = sorted(ch.origin_y for s in base_spans for ch in s.chars)
    baseline = origins[len(origins) // 2]

    pieces: list[Piece] = []
    word_starts: list[float] = []
    prev_x1: float | None = None
    after_space = True
    for s in spans:
        bold = bool(s.flags & 16) or bool(_BOLD_FONT.search(s.font))
        italic = bool(s.flags & 2) or bool(_ITALIC_FONT.search(s.font))
        colour = colour_hex(s.color)
        small = s.size < 0.85 * dom_size
        origin = s.chars[0].origin_y
        # PyMuPDF's superscript flag is a guess, and it sometimes marks a whole
        # line of normal text (a line set a little higher than its neighbour).
        # Real superscripts are short ("rd" in 3rd) or in a smaller font.
        flagged = bool(s.flags & 1) and (small or len("".join(ch.c for ch in s.chars).strip()) <= 4)
        superscript = flagged or (small and origin < baseline - 0.15 * dom_size)
        subscript = (not superscript) and small and origin > baseline + 0.08 * dom_size

        # Insert a space where there's a visible gap between spans (tabs, or
        # word-per-span justified text) unless whitespace is already there.
        first_visible = next((ch for ch in s.chars if not ch.c.isspace()), None)
        if prev_x1 is not None and pieces and first_visible is not None:
            gap = s.x0 - prev_x1
            if gap > 0.2 * dom_size and not pieces[-1].text.endswith(" ") and not s.chars[0].c.isspace():
                pieces.append(Piece(" ", False, False, False, False, False, None, dom_size, prev_x1, s.x0))

        if prev_x1 is not None and s.x0 - prev_x1 > 0.2 * dom_size:
            after_space = True  # a visible gap between spans separates words
        for ch in s.chars:
            if ch.c.isspace():
                after_space = True
            else:
                if after_space:
                    word_starts.append(ch.x0)
                after_space = False
            ul = (not ch.c.isspace()) and _underlined(ch, baseline, dom_size, segs)
            style = (bold, italic, ul, superscript, subscript, colour)
            if pieces and pieces[-1].style() == style:
                pieces[-1].text += ch.c
                pieces[-1].x1 = ch.x1
            else:
                pieces.append(Piece(ch.c, *style, s.size, ch.x0, ch.x1))
            if pieces[-1].vis_x0 is None and not ch.c.isspace():
                pieces[-1].vis_x0 = ch.x0
        prev_x1 = s.x1

    # Left edge = first visible character (leading spaces don't count)
    x_vis = [ch.x0 for s in spans for ch in s.chars if not ch.c.isspace()]
    x0 = min(x_vis) if x_vis else min(s.x0 for s in spans)
    line = Line(
        page=page_index,
        x0=x0,
        y0=min(s.y0 for s in spans),
        x1=max(s.x1 for s in spans),
        y1=max(s.y1 for s in spans),
        size=dom_size,
        pieces=pieces,
        page_width=page.rect.width,
        page_height=page.rect.height,
    )
    line.blank = not line.text.strip()
    line.text_x0 = x0
    line.word_starts = word_starts
    _detect_bullet(line, spans)
    line.level_x = line.x0
    cx, cy = (line.x0 + line.x1) / 2, (line.y0 + line.y1) / 2
    line.in_box = any(b.contains(line.x0, cy) and b.contains(cx, cy) for b in boxes)
    return line


def _underlined(ch: _Char, baseline: float, size: float, segs: list[Segment]) -> bool:
    """True if a thin horizontal segment sits just under this character's
    baseline (not through the middle of it, which would be strikethrough)."""
    cx = (ch.x0 + ch.x1) / 2
    for s in segs:
        y = (s.y0 + s.y1) / 2
        if s.x0 - 0.5 <= cx <= s.x1 + 0.5 and baseline - 0.1 * size <= y <= baseline + 0.4 * size:
            return True
    return False


def _detect_bullet(line: Line, spans: list[_Span]) -> None:
    """If the line starts with a bullet glyph, record it and strip it (the
    Google Doc gets real bullets instead)."""
    text = line.text
    stripped = text.lstrip()
    if not stripped:
        return
    first = stripped[0]
    rest = stripped[1:]
    first_font = next((s.font for s in spans for ch in s.chars if not ch.c.isspace()), "")
    is_bullet = (
        first in BULLET_GLYPHS
        or (first in DASH_BULLETS and rest[:1].isspace())
        or (bool(_SYMBOL_FONT.search(first_font)) and not first.isdigit() and bool(rest.strip()))
        # Word's second-level bullet is a Courier New "o"
        or (first == "o" and "courier" in first_font.lower() and rest[:1].isspace())
    )
    if not is_bullet:
        return

    # Glyph x-position: the first visible character of the line
    glyph_x = None
    for s in spans:
        for ch in s.chars:
            if not ch.c.isspace():
                glyph_x = ch.x0
                break
        if glyph_x is not None:
            break

    line.pieces_with_glyph = [replace(p) for p in line.pieces]

    # Remove the glyph and any whitespace directly after it from the pieces.
    to_remove = len(text) - len(stripped) + 1
    while to_remove and line.pieces:
        p = line.pieces[0]
        if len(p.text) <= to_remove:
            to_remove -= len(p.text)
            line.pieces.pop(0)
        else:
            p.text = p.text[to_remove:]
            to_remove = 0
    while line.pieces and not line.pieces[0].text.strip():
        line.pieces.pop(0)
    if line.pieces:
        line.pieces[0].text = line.pieces[0].text.lstrip()

    line.bullet = first
    line.x0 = glyph_x if glyph_x is not None else line.x0
    line.text_x0 = _first_text_x(spans, glyph_x)


def _first_text_x(spans: list[_Span], glyph_x: float | None) -> float:
    """x of the first visible character after the bullet glyph."""
    seen_glyph = False
    for s in spans:
        for ch in s.chars:
            if ch.c.isspace():
                continue
            if not seen_glyph:
                seen_glyph = True
                continue
            return ch.x0
    return glyph_x or 0.0
