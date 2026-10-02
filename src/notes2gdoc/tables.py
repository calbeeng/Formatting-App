"""Joining a table that was split across slides or pages.

Slide decks often continue a table on the next slide, repeating its header row
("Considerations in Sections 6(3) - 6(9)" on four slides in a row). Tables that
follow each other directly, with the same number of columns and the same first
row, become one table: the repeated header rows are dropped.
"""

from __future__ import annotations

import re

from .model import Block, Document, TableCell


def _row_text(table, row: int) -> str:
    return re.sub(r"\s+", " ", " | ".join(c.text for c in table.cells if c.row == row)).strip().lower()


def _same_header(a, b) -> bool:
    head = _row_text(a, 0)
    return bool(head) and a.n_cols == b.n_cols and head == _row_text(b, 0)


def merge_split_tables(doc: Document) -> None:
    out: list[Block] = []
    for b in doc.blocks:
        prev = out[-1] if out else None
        if (b.kind == "table" and prev is not None and prev.kind == "table"
                and _same_header(prev.table, b.table)):
            t, extra = prev.table, b.table
            offset = t.n_rows - 1
            for c in extra.cells:
                if c.row == 0:
                    continue
                t.cells.append(TableCell(c.row + offset, c.col, c.blocks, c.rowspan, c.colspan, c.background))
            t.n_rows += extra.n_rows - 1
            prev.note += f"; continued from page {b.page}"
            continue
        out.append(b)
    doc.blocks = out
