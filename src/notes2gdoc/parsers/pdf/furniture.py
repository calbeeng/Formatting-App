"""Remove "page furniture": running headers, footers, page numbers, copyright
lines and logo text that repeat in the page margins.

Two rules:

1. Repetition. A line in the top or bottom margin zone whose text (with digits
   masked, so "Page 3" == "Page 4") appears on at least half the pages.
   Examples: "2026 Part B Session 2 Reading List & Syllabus", "SILE Part B 2026
   Session 2", "3 | Page".
   In slide decks, slide titles live in the top zone and repeat a lot ("UNCITRAL
   Model Law" x7), so there we only remove repeated text that is *smaller* than
   the body text.

2. Patterns. Even when they appear only once, margin lines that are just a page
   number, or small copyright lines, are furniture.

3. Loose slide numbers. A line that's only a number counts as a page number if
   it sits at the page edge (right 15%, or the top/bottom margin zones), or if most pages
   have such a number that tracks the page count (slide 5 says "4", etc.).
"""

from __future__ import annotations

import math
import re

from .extract import Line

TOP_ZONE = 0.12     # top 12% of the page height
BOTTOM_ZONE = 0.10  # bottom 10%

_PAGE_NUMBER = re.compile(
    r"^\W*(page\s*)?#+(\s*(of|/)\s*#+)?(\s*\|\s*page)?\W*$", re.I
)
_COPYRIGHT = re.compile(r"copyright|©|all rights reserved", re.I)


def _key(text: str) -> str:
    t = re.sub(r"\d+", "#", text.lower())
    return re.sub(r"\s+", " ", t).strip()


def _in_zone(line: Line) -> bool:
    h = line.page_height
    in_top = line.y0 < h * TOP_ZONE
    in_bottom = line.y0 >= h * (1 - BOTTOM_ZONE)
    return in_top or in_bottom


def remove_furniture(
    pages: list[list[Line]], body_size: float, slides: bool
) -> tuple[list[list[Line]], list[Line]]:
    """Return (cleaned pages, removed lines)."""
    n = len(pages)
    counts: dict[str, set[int]] = {}
    for i, lines in enumerate(pages):
        for ln in lines:
            if not isinstance(ln, Line) or ln.blank or not _in_zone(ln):
                continue
            counts.setdefault(_key(ln.text), set()).add(i)

    threshold = max(2, math.ceil(0.5 * n))
    repeated = {k for k, pg in counts.items() if n >= 2 and len(pg) >= threshold}
    offset = _page_number_offset(pages)

    cleaned: list[list[Line]] = []
    removed: list[Line] = []
    for i, lines in enumerate(pages):
        keep = []
        for ln in lines:
            if not isinstance(ln, Line):
                keep.append(ln)
                continue
            nums = _lone_numbers(ln.text)
            at_edge = ln.x0 > 0.85 * ln.page_width or _in_zone(ln)
            if nums and (at_edge or (offset is not None and all(v - i == offset for v in nums))):
                removed.append(ln)  # a slide/page number
            elif _is_furniture(ln, repeated, body_size, slides):
                removed.append(ln)
            else:
                keep.append(ln)
        cleaned.append(keep)
    return cleaned, removed


def _lone_numbers(text: str) -> list[int]:
    """[13, 13] for "13 13"; [] unless the line is nothing but 1-3 digit numbers."""
    tokens = text.split()
    if tokens and all(t.isdigit() and len(t) <= 3 for t in tokens):
        return [int(t) for t in tokens]
    return []


def _page_number_offset(pages) -> int | None:
    """Some decks place the slide number as loose text anywhere on the slide.

    If at least 3 pages (and a third of all pages) have a line that's just a
    number, and that number minus the page index is the same on most of them
    (e.g. slide 5 shows "4"), return that offset; those lines are page numbers.
    """
    offsets: dict[int, int] = {}
    for i, lines in enumerate(pages):
        found = set()
        for ln in lines:
            if isinstance(ln, Line):
                for v in _lone_numbers(ln.text):
                    found.add(v - i)
        for off in found:
            offsets[off] = offsets.get(off, 0) + 1
    if not offsets:
        return None
    off, count = max(offsets.items(), key=lambda kv: kv[1])
    return off if count >= max(3, len(pages) / 3) else None


def _is_furniture(ln: Line, repeated: set[str], body_size: float, slides: bool) -> bool:
    if not _in_zone(ln):
        return False
    if ln.blank:
        return True  # blank lines in the margins carry no meaning
    key = _key(ln.text)
    small = ln.size < body_size * 0.95
    if key in repeated and (small or not slides):
        return True
    if _PAGE_NUMBER.match(key):
        return True
    if _COPYRIGHT.search(ln.text) and small:
        return True
    return False
