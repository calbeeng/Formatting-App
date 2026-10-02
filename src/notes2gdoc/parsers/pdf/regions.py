"""Find tables and diagrams on a page, so their text doesn't get mixed into the
normal reading flow.

Tables
------
PyMuPDF's `find_tables()` spots grids of ruled lines/cell backgrounds. We keep
its answer unless the "table" contains coloured shapes that don't line up with
its cells; that's a flowchart drawn on top of some lines, not a table.
Merged cells (e.g. one row spanning both columns) are supported.

Diagrams (flowcharts, process maps)
-----------------------------------
A diagram = at least two filled/outlined shapes that each contain text
(boxes like "Identify the target", "Pre-Acquisition"), plus at least one arrow
or connector between them. The diagram's area is
those boxes plus any arrows/connectors touching them, and all text inside that
area belongs to the diagram. We render that area as a picture, and also keep its
text (grouped per box) so it can go into the doc as bullets underneath.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf as fitz

from .layout import THIN

MIN_SHAPE_W, MIN_SHAPE_H = 20, 12
# Diagram picture resolution. 170 dpi is sharp on screen without huge files.
DIAGRAM_DPI = 170
DIAGRAM_PADDING = 6  # points around the diagram when cropping


@dataclass
class CellSpec:
    row: int
    col: int
    rect: fitz.Rect
    rowspan: int = 1
    colspan: int = 1
    lines: list = field(default_factory=list)  # list[Line], filled in by extract.py


@dataclass
class TableRegion:
    rect: fitz.Rect
    n_rows: int
    n_cols: int
    cells: list[CellSpec]
    col_widths: list[float] = field(default_factory=list)  # in points, left to right

    @property
    def y0(self) -> float:
        return self.rect.y0


@dataclass
class DiagramRegion:
    rect: fitz.Rect
    shapes: list[fitz.Rect]          # boxes that contain text, for grouping
    png: bytes = b""
    lines: list = field(default_factory=list)   # list[Line], filled in by extract.py
    # Filled in by extract.py: text grouped per box, and text outside any box
    groups: list = field(default_factory=list)  # list[(box rect, list[Line])]
    labels: list = field(default_factory=list)  # list[Line]

    @property
    def y0(self) -> float:
        return self.rect.y0


def _near_white(rgb) -> bool:
    return rgb is not None and all(c > 0.94 for c in rgb)


def _is_thin(r: fitz.Rect) -> bool:
    return r.width <= THIN or r.height <= THIN


def find_tables(page: fitz.Page, drawings: list[dict]) -> list[TableRegion]:
    try:
        found = page.find_tables()
    except Exception:  # table finder is heuristic; never let it break parsing
        return []
    out = []
    for tb in found.tables:
        if tb.row_count * tb.col_count < 2:
            continue  # a single bordered box is a "notice box", handled elsewhere
        rect = fitz.Rect(tb.bbox)
        rows = [row.cells for row in tb.rows]
        cell_rects = [fitz.Rect(c) for row in rows for c in row if c]
        if _contains_foreign_shapes(rect, cell_rects, drawings):
            continue

        # Column/row edges from all cells, to work out spans of merged cells
        xs = sorted({round(r.x0) for r in cell_rects} | {round(r.x1) for r in cell_rects})
        ys = sorted({round(r.y0) for r in cell_rects} | {round(r.y1) for r in cell_rects})
        col_edges = _dedupe(xs)
        row_edges = _dedupe(ys)
        n_cols = max(1, len(col_edges) - 1)
        n_rows = max(1, len(row_edges) - 1)
        cells = []
        for r in cell_rects:
            c0 = _index(col_edges, r.x0)
            c1 = _index(col_edges, r.x1)
            r0 = _index(row_edges, r.y0)
            r1 = _index(row_edges, r.y1)
            cells.append(CellSpec(r0, c0, r, rowspan=max(1, r1 - r0), colspan=max(1, c1 - c0)))
        widths = [col_edges[i + 1] - col_edges[i] for i in range(len(col_edges) - 1)]
        out.append(TableRegion(rect, n_rows, n_cols, cells, widths if len(widths) == n_cols else []))
    return out


def _dedupe(values: list[int], tol: int = 3) -> list[int]:
    out: list[int] = []
    for v in values:
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _index(edges: list[int], v: float) -> int:
    return min(range(len(edges)), key=lambda i: abs(edges[i] - v))


def _contains_foreign_shapes(rect, cell_rects, drawings) -> bool:
    """True if coloured shapes inside the table don't match its cells."""
    for d in drawings:
        if d.get("fill") is None or _near_white(d.get("fill")):
            continue
        r = d["rect"]
        if _is_thin(r) or r.width * r.height >= rect.width * rect.height * 0.98:
            continue
        if not rect.contains(fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)):
            continue
        matches_cell = any(
            abs(r.x0 - c.x0) < 4 and abs(r.x1 - c.x1) < 4 and abs(r.y0 - c.y0) < 4 and abs(r.y1 - c.y1) < 4
            for c in cell_rects
        )
        # A cell background may also cover a whole row or column of cells
        covers_cells = any(r.contains(c) for c in cell_rects)
        if not (matches_cell or covers_cells):
            return True
    return False


def find_diagrams(
    page: fitz.Page, drawings: list[dict], text_rects: list[fitz.Rect], exclude: list[fitz.Rect]
) -> list[DiagramRegion]:
    page_area = page.rect.width * page.rect.height

    def excluded(r: fitz.Rect) -> bool:
        centre = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        return any(e.contains(centre) for e in exclude)

    shapes: list[fitz.Rect] = []
    others: list[fitz.Rect] = []   # arrows, connector lines
    for d in drawings:
        r = fitz.Rect(d["rect"])
        if r.width * r.height > 0.5 * page_area or excluded(r):
            continue
        fill, stroke = d.get("fill"), d.get("color")
        # Page-wide bands (header/footer bars) are decoration, not boxes
        big_enough = MIN_SHAPE_W <= r.width < 0.9 * page.rect.width and r.height >= MIN_SHAPE_H
        filled_box = fill is not None and (not _near_white(fill) or stroke is not None)
        outlined_box = fill is None and stroke is not None  # e.g. the "Settlement" box
        is_box = big_enough and (filled_box or outlined_box)
        if is_box:
            shapes.append(r)
        elif "s" in d.get("type", "") or (fill is not None and not _near_white(fill)):
            others.append(r)

    def centre_in(r: fitz.Rect, t: fitz.Rect) -> bool:
        return r.contains(fitz.Point((t.x0 + t.x1) / 2, (t.y0 + t.y1) / 2))

    text_shapes = [s for s in shapes if any(centre_in(s, t) for t in text_rects)]
    # Drop shapes that sit inside a bigger text shape (e.g. a highlight inside a box)
    text_shapes = [s for s in text_shapes if not any(o != s and o.contains(s) for o in text_shapes)]
    if len(text_shapes) < 2:
        return []

    region = fitz.Rect(text_shapes[0])
    for s in text_shapes[1:]:
        region |= s

    # A flowchart has at least one arrow/connector between its boxes. Without
    # one it's just side-by-side text boxes (a comparison), which reads fine as
    # ordinary text.
    # The connector's centre must lie within the area the boxes span (so a rule
    # above or below all the boxes doesn't count) and not inside a box.
    connectors = [
        r for r in shapes + others
        if r not in text_shapes
        and region.contains(fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
        and r.width < 0.9 * region.width
        and not any(t.contains(r) for t in text_shapes)
    ]
    if not connectors:
        return []
    # Pull in arrows/connectors/other shapes that touch the region (twice, so
    # chains like box -> line -> arrowhead get included).
    for _ in range(2):
        grown = fitz.Rect(region.x0 - 20, region.y0 - 20, region.x1 + 20, region.y1 + 20)
        for r in shapes + others:
            if grown.intersects(r) and r.width < page.rect.width * 0.98:
                region |= r
    # Text partly inside the region (labels like "Pre: 100%") belongs to it too
    for t in text_rects:
        if centre_in(region, t):
            region |= t

    clip = fitz.Rect(
        region.x0 - DIAGRAM_PADDING, region.y0 - DIAGRAM_PADDING,
        region.x1 + DIAGRAM_PADDING, region.y1 + DIAGRAM_PADDING,
    ) & page.rect
    png = page.get_pixmap(clip=clip, dpi=DIAGRAM_DPI).tobytes("png")
    return [DiagramRegion(clip, text_shapes, png)]
