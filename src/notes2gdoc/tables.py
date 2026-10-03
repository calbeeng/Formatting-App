"""Joining a table that was split across slides or pages.

Slide decks often continue a table on the next slide, repeating its header row
("Considerations in Sections 6(3) - 6(9)" on four slides in a row). Tables that
follow each other directly, with the same number of columns and the same first
row, become one table: the repeated header rows are dropped. On slides, a
table on the next slide with the same columns and no title row of its own
also carries on the one before (one table, matching column widths).
"""

from __future__ import annotations

import re

from .model import Block, Document, TableCell


def _row_text(table, row: int) -> str:
    return re.sub(r"\s+", " ", " | ".join(c.text for c in table.cells if c.row == row)).strip().lower()


def _same_header(a, b) -> bool:
    head = _row_text(a, 0)
    return bool(head) and a.n_cols == b.n_cols and head == _row_text(b, 0)


def _has_header(t) -> bool:
    """First row is one cell across the whole table (a title row)."""
    first = [c for c in t.cells if c.row == 0]
    return len(first) == 1 and first[0].colspan == t.n_cols


def _continues(prev: Block, b: Block, last_page: int, layout: str) -> bool:
    """On slides, a table with the same columns on the next slide, without a
    title row of its own, carries on the one before ("Section 12(3) | …"
    then "Section 12(4) & (5) | …")."""
    return (layout == "slides" and prev.table.n_cols == b.table.n_cols >= 2
            and not _has_header(prev.table) and not _has_header(b.table)
            and 0 <= b.page - last_page <= 1 and _similar_columns(prev.table, b.table))


def _similar_columns(a, b) -> bool:
    """Column widths in about the same proportions (a narrow "Section 13(1)"
    label column doesn't match two equal "Personal Welfare | Property &
    Affairs" columns)."""
    if not a.col_widths or not b.col_widths:
        return True
    sa, sb = sum(a.col_widths), sum(b.col_widths)
    return all(abs(x / sa - y / sb) <= 0.15 for x, y in zip(a.col_widths, b.col_widths))


def merge_split_tables(doc: Document) -> None:
    out: list[Block] = []
    last_page: dict[int, int] = {}   # merged table -> page of its last part
    for b in doc.blocks:
        prev = out[-1] if out else None
        if b.kind == "table" and prev is not None and prev.kind == "table":
            lp = last_page.get(id(prev), prev.page)
            same = _same_header(prev.table, b.table)
            if same or _continues(prev, b, lp, doc.layout):
                t, extra = prev.table, b.table
                skip = 1 if same else 0   # the repeated title row
                offset = t.n_rows - skip
                for c in extra.cells:
                    if c.row < skip:
                        continue
                    t.cells.append(TableCell(c.row + offset, c.col, c.blocks, c.rowspan, c.colspan, c.background))
                t.n_rows += extra.n_rows - skip
                prev.note += f"; continued from page {b.page}"
                last_page[id(prev)] = b.page
                continue
        out.append(b)
    doc.blocks = out
