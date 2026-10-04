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
    # Ruled tables: x where each column's text starts. A text line running
    # across one of these (a label and its text on one line) is cut there.
    splits: list[float] = field(default_factory=list)
    ruled: bool = False  # found by find_ruled_tables (rules only, no grid)

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


# Ruled tables: full-width horizontal lines between rows, no vertical lines
# (common in slide decks). A rule must span this share of the page width.
RULE_MIN_WIDTH = 0.6
# The gap between the two columns must be at least this wide (points)
RULED_MIN_GAP = 3
# Rows sharing a column gap + lines starting at its right edge
COLUMN_SUPPORT = 3


def find_ruled_tables(page: fitz.Page, drawings: list[dict], texts: list[tuple[fitz.Rect, list[float], list[tuple[float, float]]]],
                      exclude: list[fitz.Rect]) -> list[TableRegion]:
    """Tables drawn with horizontal rules only, like

        ─────────────────────────────────────────────
              Considerations in Sections 6(3) – 6(9)       <- header (one cell)
        ─────────────────────────────────────────────
         Section 6(3)     (a) Whether it is likely …
         Future Capacity  (b) If it appears likely …
        ─────────────────────────────────────────────

    `texts` holds each text line's box and the x positions where its style
    changes at the start of a word (see extract._style_breaks).

    * Rows are the bands between rules; the text below the last rule can
      be a last row too.
    * Column boundaries come from empty vertical gaps between the text in a
      row. A gap counts only if no line in any row runs across it (rows of a
      single line, maybe a header, don't count), unless that
      line changes style exactly at the gap's right edge. That happens when a
      bold label and its text share a line ("**Section 3(2)** Assume person
      has capacity"); such lines are split there (TableRegion.splits).
    * A row with a single line that runs across a boundary, or that sits
      clear of the first column (a centred title), is a header spanning all
      columns.
    * At least one row must have text in two columns, or it's just rules."""
    width = page.rect.width
    rules = sorted(
        (fitz.Rect(d["rect"]) for d in drawings
         if d["rect"].height <= THIN and d["rect"].width >= RULE_MIN_WIDTH * width),
        key=lambda r: r.y0,
    )
    ys: list[float] = []
    for r in rules:
        if not ys or r.y0 - ys[-1] > 3:
            ys.append((r.y0 + r.y1) / 2)
    if not ys:
        return []
    x0 = min(r.x0 for r in rules)
    x1 = max(r.x1 for r in rules)
    if any(fitz.Rect(x0, ys[0], x1, ys[-1]).intersects(e) for e in exclude):
        return []

    bands = []
    for top, bottom in zip(ys, ys[1:]):
        rows = [(r, w, ws) for r, w, ws in texts if top < (r.y0 + r.y1) / 2 < bottom and x0 <= r.x0 and r.x1 <= x1 + 2]
        if rows:
            bands.append((top, bottom, rows))

    region = _table_from_bands(bands, x0, x1) if bands else None
    # The last row may have no rule under it (Powers of Court: rules only
    # around the "Personal Welfare | Property & Affairs" header; Deputies:
    # the "Section 21" row runs to the bottom of the slide). Try again with
    # the text below the last rule as a final row, and keep that if it fits
    # the same columns (or makes a table where there was none).
    below = [t for t in texts if (t[0].y0 + t[0].y1) / 2 > ys[-1]
             and (t[0].y0 + t[0].y1) / 2 <= page.rect.height and x0 - 2 <= t[0].x0 and t[0].x1 <= x1 + 2]
    if below:
        bottom = max(t[0].y1 for t in below) + 2
        longer = _table_from_bands(bands + [(ys[-1], bottom, below)], x0, x1)
        footer = longer is not None and longer.n_cols > 1 and any(
            c.row == longer.n_rows - 1 and c.colspan == longer.n_cols for c in longer.cells)
        if longer is not None and not footer and (region is None or longer.n_cols == region.n_cols):
            region = longer
    if region is None and bands:
        region = _one_column_table(bands, x0, x1)
    if region is not None:
        region.ruled = True
    return [region] if region else []


def _one_column_table(bands, x0: float, x1: float) -> TableRegion | None:
    """A centred heading between two rules with a block of text under it
    ("Voluntary" / its provisions): a one-column table with a header row."""
    if len(bands) < 2 or len(bands[0][2]) != 1:
        return None
    head = bands[0][2][0][0]
    centre = (x0 + x1) / 2
    if abs((head.x0 + head.x1) / 2 - centre) > 0.08 * (x1 - x0) or head.width > 0.6 * (x1 - x0):
        return None
    cells = [CellSpec(i, 0, fitz.Rect(x0, top, x1, bottom)) for i, (top, bottom, _) in enumerate(bands)]
    return TableRegion(fitz.Rect(x0, bands[0][0], x1, bands[-1][1]), len(bands), 1, cells, [x1 - x0])


def _table_from_bands(bands, x0: float, x1: float) -> TableRegion | None:
    """Columns and cells for rows of text between rules (see find_ruled_tables)."""
    def crosses(item, mid, edge) -> bool:
        r, breaks, _ = item
        return r.x0 < mid < r.x1 and not any(abs(w - edge) <= 4 for w in breaks)

    def splits_word(item, mid, edge) -> bool:
        """Like crosses(), but only if a word runs across `mid`: a line the
        PDF happens to store across both columns ("… donor / " + "NOTE:")
        has a space there."""
        return crosses(item, mid, edge) and any(a < mid < b for a, b in item[2])

    # Candidate gaps from every row with 2+ lines; keep those no row crosses
    ok: list[tuple[float, float]] = []
    for _, _, rows in bands:
        spans = sorted((r.x0, r.x1) for r, *_ in rows)
        reach = spans[0][1]
        for a, b in spans[1:]:
            if a - reach >= RULED_MIN_GAP:
                gap = (reach, a)
                mid = (gap[0] + gap[1]) / 2
                # (one-line rows may be headers running across: no veto)
                if all(not splits_word(it, mid, gap[1]) for _, _, rs in bands if len(rs) > 1 for it in rs):
                    ok.append(gap)
            reach = max(reach, b)
    if not ok:
        return None
    # Overlapping gaps are the same column boundary: keep their common part
    # and count the rows that have it
    ok.sort()
    merged: list[list[float]] = []
    for a, b in ok:
        if merged and a < merged[-1][1]:
            merged[-1] = [max(a, merged[-1][0]), min(b, merged[-1][1]), merged[-1][2] + 1]
        else:
            merged.append([a, b, 1])
    # A real column edge is backed up: by several rows, or by several lines
    # starting right at it. (A stray short word, like "of" left behind when
    # "Disposition of property" wraps, leaves a one-off gap.)
    in_table = [it for _, _, rs in bands for it in rs]
    merged = [
        (a, b) for a, b, n in merged
        if n + sum(1 for r, brk, _ in in_table
                   if abs(r.x0 - b) <= 4 or any(abs(x - b) <= 4 for x in brk)) >= COLUMN_SUPPORT
    ]
    if not merged:
        return None
    edges = [b for _, b in merged]                 # where each column's text starts
    mids = [(a + b) / 2 for a, b in merged]
    bounds = [x0, *mids, x1]
    n_cols = len(bounds) - 1

    def column(r: fitz.Rect) -> int:
        cx = (r.x0 + r.x1) / 2
        return next(i for i in range(n_cols) if cx <= bounds[i + 1] or i == n_cols - 1)

    cells: list[CellSpec] = []
    multi_column = False
    for row, (top, bottom, rows) in enumerate(bands):
        across = any(crosses(it, m, e) for it in rows for m, e in zip(mids, edges))
        if across or (len(rows) == 1 and rows[0][0].x0 > bounds[1]):
            cells.append(CellSpec(row, 0, fitz.Rect(x0, top, x1, bottom), colspan=n_cols))
            continue
        used = set()
        for r, *_ in rows:
            used.add(column(r))
            # a line cut at a boundary fills both sides
            used.update(i + 1 for i, m in enumerate(mids) if r.x0 < m < r.x1)
        multi_column |= len(used) >= 2
        for c in range(n_cols):
            cells.append(CellSpec(row, c, fitz.Rect(bounds[c], top, bounds[c + 1], bottom)))
    if not multi_column:
        return None
    rect = fitz.Rect(x0, bands[0][0], x1, bands[-1][1])
    widths = [bounds[i + 1] - bounds[i] for i in range(n_cols)]
    return TableRegion(rect, len(bands), n_cols, cells, widths, splits=edges)


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


# Pictures (photos, scanned charts) embedded in the PDF. Smaller than this
# share of the page = an icon or bullet image; bigger = a page background.
PICTURE_MIN_AREA = 0.02
PICTURE_MAX_AREA = 0.7


def repeated_images(pages: list[fitz.Page]) -> set[int]:
    """Images on many pages (a logo, a background): page decoration."""
    if len(pages) < 3:
        return set()
    counts: dict[int, int] = {}
    for page in pages:
        for xref in {im[0] for im in page.get_images(full=True)}:
            counts[xref] = counts.get(xref, 0) + 1
    return {x for x, n in counts.items() if n >= max(3, 0.3 * len(pages))}


def find_pictures(page: fitz.Page, skip: set[int], exclude: list[fitz.Rect],
                  text_rects: list[fitz.Rect]) -> list[DiagramRegion]:
    """Each picture becomes a DiagramRegion (cropped from the page, so masks
    and cropping look the way they do in the PDF). Left out:
    * pictures with text on top of them: a background behind the slide's
      text (a texture, a title banner), not content;
    * short banners in the top quarter of the page: a logo;
    * anything over 3x wider than tall: a banner or a title drawn as a
      picture (which can't become a heading anyway)."""
    area = page.rect.width * page.rect.height
    out: list[DiagramRegion] = []
    for im in page.get_images(full=True):
        if im[0] in skip:
            continue
        try:
            rects = page.get_image_rects(im[0])
        except Exception:
            continue
        for r in rects:
            r = fitz.Rect(r) & page.rect
            if r.is_empty or not PICTURE_MIN_AREA * area <= r.width * r.height <= PICTURE_MAX_AREA * area:
                continue
            centre = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            if any(e.contains(centre) for e in exclude + [o.rect for o in out]):
                continue
            if any(r.contains(fitz.Point((t.x0 + t.x1) / 2, (t.y0 + t.y1) / 2)) for t in text_rects):
                continue
            if r.y1 < page.rect.height * 0.3 and r.height < page.rect.height * 0.25:
                continue
            if r.width > 3 * r.height:  # a banner, e.g. a section title drawn as a picture
                continue
            png = page.get_pixmap(clip=r, dpi=DIAGRAM_DPI).tobytes("png")
            out.append(DiagramRegion(r, [], png))
    return out


def add_title_row(table: TableRegion, rect: fitz.Rect) -> None:
    """Make the text in `rect` (just above the table) its first row, one cell
    across the whole width."""
    for c in table.cells:
        c.row += 1
    table.cells.insert(0, CellSpec(0, 0, fitz.Rect(table.rect.x0, rect.y0 - 1, table.rect.x1, table.rect.y0),
                                   colspan=table.n_cols))
    table.n_rows += 1
    table.rect = fitz.Rect(table.rect.x0, rect.y0 - 1, table.rect.x1, table.rect.y1)


def find_box_rows(page: fitz.Page, skip: set[int], text_rects: list[fitz.Rect],
                  exclude: list[fitz.Rect]) -> list[TableRegion]:
    """Two or more picture boxes side by side with text on them (three
    rounded boxes: "no reasonable cause of action" | "abuse of process…" |
    "…in the interests of justice…"). Read as plain text, their lines would
    run into each other, so the row becomes a one-row table, a cell per box."""
    area = page.rect.width * page.rect.height
    boxes: list[fitz.Rect] = []
    for im in page.get_images(full=True):
        if im[0] in skip:
            continue
        try:
            rects = page.get_image_rects(im[0])
        except Exception:
            continue
        for r in rects:
            r = fitz.Rect(r) & page.rect
            if r.is_empty or not 0.01 * area <= r.width * r.height <= 0.4 * area:
                continue
            centre = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            if any(e.contains(centre) for e in exclude):
                continue
            if any(r.contains(fitz.Point((t.x0 + t.x1) / 2, (t.y0 + t.y1) / 2)) for t in text_rects):
                boxes.append(r)
    boxes.sort(key=lambda r: r.x0)
    out: list[TableRegion] = []
    used: set[int] = set()
    for i, r in enumerate(boxes):
        if i in used:
            continue
        row = [r]
        for j in range(i + 1, len(boxes)):
            o = boxes[j]
            if (j not in used and abs(o.y0 - r.y0) <= 15 and abs(o.height - r.height) <= 0.25 * r.height
                    and o.x0 >= row[-1].x1 - 5):
                row.append(o)
                used.add(j)
        if len(row) < 2:
            continue
        rect = fitz.Rect(row[0])
        for o in row[1:]:
            rect |= o
        cells = [CellSpec(0, c, fitz.Rect(o)) for c, o in enumerate(row)]
        out.append(TableRegion(rect, 1, len(row), cells, [1.0] * len(row)))
    return out
