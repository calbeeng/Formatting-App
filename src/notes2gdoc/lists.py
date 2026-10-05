"""Turning typed list numbers ("1.", "a.", "(b)", "(ii)") into real Google
Docs numbered lists.

Runs over the finished Document (any file type), after parsing:

1. Find runs of consecutive paragraphs that start with a list marker.
2. Work out each marker's type: decimal (1, 2), lower/upper letters (a, A),
   lower/upper roman (i, I). "i", "v", "x" etc. are ambiguous; a letter is
   preferred when it continues a letter sequence ("h" -> "i"), roman when it
   continues a roman one ("iv" -> "v") or starts one ("i").
3. Need at least two items, and check the numbers really count up from the start (1/a/i, then +1 each,
   restarting under each parent item). If they don't (e.g. an excerpt that
   starts at "(3)"), the run is left alone so the literal numbers stay correct.
4. Convert: the paragraph becomes a numbered list item, the typed marker is
   removed (Google Docs draws the number), and the nesting level is chosen so
   Google's numbering style shows the same kind of number: in the
   "1. / a. / i." style, level 0 shows 1, 2, 3; level 1 shows a, b, c; level 2
   shows i, ii, iii (then it repeats).

Headings are never touched.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .model import Block, Document, Run, merge_runs
from .numbering import int_to_roman, is_roman, roman_to_int

_MARKER = re.compile(r"^\s*(?:\((?P<p>[A-Za-z]{1,5}|\d{1,3})\)|(?P<d>[A-Za-z]{1,5}|\d{1,3})[.)])(?=\s)")

# Google Docs numbered-list presets, and which kind of number each level shows
# (the pattern repeats every 3 levels)
PRESETS = {
    "NUMBERED_DECIMAL_ALPHA_ROMAN": ("decimal", "alpha", "roman"),
    "NUMBERED_UPPERALPHA_ALPHA_ROMAN": ("ALPHA", "alpha", "roman"),
    "NUMBERED_UPPERROMAN_UPPERALPHA_DECIMAL": ("ROMAN", "ALPHA", "decimal"),
}
_PRESET_FOR_TOP = {"decimal": "NUMBERED_DECIMAL_ALPHA_ROMAN", "alpha": "NUMBERED_DECIMAL_ALPHA_ROMAN",
                   "roman": "NUMBERED_DECIMAL_ALPHA_ROMAN", "ALPHA": "NUMBERED_UPPERALPHA_ALPHA_ROMAN",
                   "ROMAN": "NUMBERED_UPPERROMAN_UPPERALPHA_DECIMAL"}


def marker_of(text: str) -> tuple[str, str] | None:
    """(token, matched marker text) for a line starting with a list marker."""
    m = _MARKER.match(text)
    if not m:
        return None
    tok = m.group("p") or m.group("d")
    if tok.isalpha() and _alpha_value(tok) is None and not is_roman(tok.lower()):
        return None  # "MR. Smith", "Dr. Tan": not a list letter
    return tok, m.group(0)


def _alpha_value(tok: str) -> int | None:
    t = tok.lower()
    if len(set(t)) != 1 or not t.isalpha():
        return None
    return (ord(t[0]) - 96) + 26 * (len(t) - 1)


def _classify(tok: str, last: dict[str, int]) -> tuple[str, int] | None:
    """(type, value) for one marker, given the last value seen per type."""
    if tok.isdigit():
        return "decimal", int(tok)
    upper = tok.isupper()
    a_val = _alpha_value(tok)
    r_val = roman_to_int(tok) if is_roman(tok.lower()) else None
    a_type, r_type = ("ALPHA", "ROMAN") if upper else ("alpha", "roman")
    if a_val is not None and r_val is not None:
        if r_type in last and r_val == last[r_type] + 1:
            return r_type, r_val
        if a_type in last and a_val == last[a_type] + 1:
            return a_type, a_val
        if r_val == 1:
            return r_type, 1
        return a_type, a_val
    if r_val is not None:
        return r_type, r_val
    if a_val is not None:
        return a_type, a_val
    return None


def _strip_marker(runs: list[Run], marker_text: str) -> list[Run]:
    """Remove the typed marker (and the space after it) from the runs."""
    remaining = len(marker_text)
    out: list[Run] = []
    for r in runs:
        if remaining <= 0:
            out.append(r)
            continue
        if len(r.text) <= remaining:
            remaining -= len(r.text)
            continue
        out.append(replace(r, text=r.text[remaining:]))
        remaining = 0
    if out:
        out[0] = replace(out[0], text=out[0].text.lstrip())
    return [r for r in out if r.text]


# After this many numbers in a row that don't fit, the list is taken to have
# ended (rather than having a few stray lines in it).
MAX_STRAYS = 3


def _convert_run(items: list[Block], flat: bool = False, after_heading: set[int] | None = None) -> None:
    """Convert a run of list items, coping with numbers that don't count up:

    * a number that starts again (a second "1." after a list ending "3.")
      begins a new list;
    * a stray number in the middle ("ii. Originating Application" used as a
      label between items 26 and 27) is left as plain text inside the list,
      and the list carries on after it;
    * several misfits in a row end the list.
    So one odd number only affects itself, not a whole document of lists."""
    start = 0
    last: dict[str, int] = {}
    order: list[str] = []
    rank = 0                    # nesting depth of the last item taken
    after_heading = after_heading or set()
    strays: list[int] = []      # indexes skipped in the current list
    streak: list[int] = []      # misfits in a row, most recent last

    def close(end: int) -> None:
        part = [b for k, b in enumerate(items[start:end], start) if k not in strays]
        if part and _convert_list(part, flat) and start:
            part[0].list_start = True

    i = 0
    while i < len(items):
        tok = marker_of(items[i].text)[0]
        typed = _classify(tok, last)
        fits = typed is not None and (i == start or typed[1] == last.get(typed[0], 0) + 1)
        # A sub-level can't start after a heading: "a." on the next slide
        # doesn't belong to "3." on this one
        deeper = (fits and i > start and id(items[i]) in after_heading
                  and (typed[0] not in order or order.index(typed[0]) > rank))
        if deeper:
            fits = False
        if not fits:
            fresh = _classify(tok, {})
            # (after a heading, "i. Introduction" may just be a label: skip it,
            # and only end the list if more such lines follow)
            restarts = not deeper and fresh is not None and fresh[1] == 1
            streak.append(i)
            if restarts or len(streak) >= MAX_STRAYS:
                first = i if restarts else streak[0]
                strays = [k for k in strays if k < first]
                close(first)
                start, last, order, strays, streak, rank = first, {}, [], [], [], 0
                i = first
                typed = _classify(marker_of(items[i].text)[0], last)
                if typed is None:
                    start = i = i + 1
                    continue
            else:
                strays.append(i)
                i += 1
                continue
        else:
            streak = []
        typ, val = typed
        if order and typ not in order and typ not in PRESETS[_PRESET_FOR_TOP[order[0]]]:
            # A kind of number this list style has no level for ("A." under
            # "1. / a. / i."): typed text inside the list
            strays.append(i)
            i += 1
            continue
        if typ not in order:
            order.append(typ)
        rank = order.index(typ)
        last[typ] = val
        for sub in order[rank + 1:]:  # sub-lists restart under a new parent
            last.pop(sub, None)
        i += 1
    close(len(items))


def _convert_list(items: list[Block], flat: bool = False) -> bool:
    """Convert one list, if its numbering is consistent. A lone
    "(1) Purpose: …" paragraph isn't a list, so it needs 2+ items."""
    if len(items) < 2:
        return False
    parsed = []
    last: dict[str, int] = {}
    order: list[str] = []        # types in nesting order, outermost first
    for b in items:
        tok, mtext = marker_of(b.text)
        typed = _classify(tok, last)
        if typed is None:
            return False
        typ, val = typed
        if typ not in order:
            order.append(typ)
        rank = order.index(typ)
        expected = last.get(typ, 0) + 1
        if val != expected:
            return False  # doesn't count up properly: keep the literal numbers
        last[typ] = val
        for deeper in order[rank + 1:]:  # sub-lists restart under a new parent
            last.pop(deeper, None)
        parsed.append((b, mtext, typ, rank))

    preset = _PRESET_FOR_TOP[order[0]]
    cycle = PRESETS[preset]
    levels: dict[str, int] = {}
    prev_level = -1
    for typ in order:  # each kind of number gets the first suitable level below its parent
        if typ not in cycle:
            return False
        lvl = cycle.index(typ)
        while lvl <= prev_level:
            lvl += 3
        levels[typ] = prev_level = lvl
    # A list with no top-level item (just "a.", "b.", e.g. in a table cell):
    # Google Docs ignores the nesting then and shows "1., 2." pushed to the
    # right, so keep the typed letters instead.
    if min(levels.values()) > 0:
        return False
    # In table cells Google Docs doesn't nest list items reliably, so only
    # single-level lists are converted there (`flat`)
    if flat and max(levels.values()) > 0:
        return False
    for b, mtext, typ, _rank in parsed:
        b.kind = "bullet"
        b.numbered = preset
        b.level = levels[typ]
        b.runs = _strip_marker(b.runs, mtext)
        b.note = (b.note + "; " if b.note else "") + f"numbered list ({typ})"
    return True


def convert_numbered_lists(doc: Document, hang: bool | None = None) -> None:
    """`hang`: lay out the typed numbers that can't be real lists like a list
    (slides and PDFs; Word files carry their own indents)."""
    slides = doc.layout == "slides"
    if hang is None:
        hang = slides
    # Google Docs nests list items in table cells unreliably, so on slides
    # lists in cells keep their typed numbers (laid out like a list below)
    _convert_blocks(doc.blocks, False, cells=not slides, gaps=hang)
    if hang:
        _hang_literal_items(doc.blocks, slides=slides)


def _hang_literal_items(blocks: list[Block], in_cell: bool = False, slides: bool = True) -> None:
    """Typed list items left as text (a lone "1." before a table, "2."-"4."
    after it, lists in table cells) are still laid out like a list: number
    hanging, wrapped lines indented, letters one level in from numbers. The
    level is the order each kind of number first appears in the run
    (1. -> a. -> i.). Bullets between items nest under the item above.
    A table cell with a single numbered line ("1. Functional Component", a
    row label) isn't a list and is left alone."""
    if in_cell and sum(1 for b in blocks if _is_item(b)) < 2:
        return
    order: list[str] = []
    last: dict[str, int] = {}
    item_level: int | None = None   # level of the last item, while in a run
    base = 0
    for b in blocks:
        if b.in_list:
            continue  # already placed inside a real list
        if _is_item(b):
            typed = _classify(marker_of(b.text)[0], last)
            if typed is None:
                continue
            typ, val = typed
            last[typ] = val
            if typ not in order:
                order.append(typ)
            if item_level is None:
                base = b.level  # already indented (e.g. under a bullet): keep that
            b.hanging = True
            b.level = item_level = base + order.index(typ)
            continue
        if item_level is not None and b.kind == "bullet" and not b.numbered:
            b.level += item_level + 1
            continue
        item_level = None
        # Which kind of number sits at which level is kept across plain text
        # in between ("(a)" after a paragraph is still one level in). It
        # starts afresh at a slide's heading or a table; in a document it
        # holds throughout (an outline: 1. -> (a) -> i. on every page).
        if b.kind == "table" or (slides and b.kind == "heading"):
            order, last = [], {}
        if b.kind == "table":
            for cell in b.table.cells:
                _hang_literal_items(cell.blocks, in_cell=True, slides=slides)


def _is_item(b: Block | None) -> bool:
    return b is not None and b.kind == "paragraph" and not b.spacer and bool(marker_of(b.text))


def _is_inside(b: Block) -> bool:
    """Content that can sit between two items of a list: an ordinary bullet or
    an indented paragraph (a sub-point or note under the item above)."""
    return (b.kind == "bullet" and not b.numbered) or (b.kind == "paragraph" and b.level > 0 and not b.spacer)


def _is_gap(b: Block) -> bool:
    """Something a list can carry on past: a heading or a plain paragraph
    ("B. Sources of Civil Procedural Law" between items 7 and 8). Tables and
    pictures end a list: Google Docs can't continue one across them."""
    return b.kind in ("heading", "paragraph")


def _convert_blocks(blocks: list[Block], in_cell: bool, cells: bool = True, gaps: bool = True) -> None:
    """`gaps`: a list may carry on past headings and plain text (PDFs and
    slides). Without it (Word files) only bullets and indented text can sit
    between a list's items."""
    run: list[Block] = []
    for b in blocks + [None]:
        if _is_item(b) or (run and b is not None and (_is_inside(b) or (gaps and _is_gap(b)))):
            run.append(b)
            continue
        if run:
            _convert_with_inside(run, in_cell)
            run = []
        if b is not None and b.kind == "table":
            for cell in b.table.cells:
                if cells:
                    _convert_blocks(cell.blocks, True, gaps=gaps)


def _convert_with_inside(seq: list[Block], in_cell: bool) -> None:
    """Convert the list items in `seq`; content between two items of the same
    converted list stays inside it: text and bullets indented under the item
    above, headings as they are, with the numbering carrying on after."""
    items = [b for b in seq if _is_item(b)]
    # Items that come after a heading: they can continue the list ("8." after
    # "B. Sources …") but can't open a new sub-level of an item before it
    after_heading: set[int] = set()
    seen_heading = False
    for b in seq:
        if b.kind == "heading":
            seen_heading = True
        elif _is_item(b):
            if seen_heading:
                after_heading.add(id(b))
            seen_heading = False
    _convert_run(items, in_cell, after_heading)
    last: Block | None = None
    for i, b in enumerate(seq):
        if b.numbered:
            last = b
            continue
        # (a stray numbered line that didn't join the list counts as text in it)
        nxt = next((x for x in seq[i + 1:] if x.numbered), None)
        if last is None or nxt is None or not nxt.numbered or nxt.list_start or nxt.numbered != last.numbered:
            continue
        b.in_list = True
        if b.kind == "heading" or b.spacer:
            # the list carries on after it; the heading itself is untouched
            b.note = (b.note + "; " if b.note else "") + "numbered list continues after this"
            continue
        if b.kind == "bullet":
            b.level = last.level + 1 + b.level   # stays a bullet, nested under its item
        elif b.level == 0:
            b.level = last.level + 1             # text belonging to the item above
        else:
            b.level = last.level + 1 + max(0, b.level - 1)
        b.note = (b.note + "; " if b.note else "") + "inside the numbered list above"


def display_labels(blocks: list[Block]) -> dict[int, str]:
    """The number Google Docs will show for each numbered item (for previews).
    Keyed by id(block)."""
    labels: dict[int, str] = {}
    counters: dict[int, int] = {}
    prev = None
    for b in blocks:
        if b.in_list:
            continue  # the list carries on after it
        if b.kind != "bullet" or not b.numbered:
            counters.clear()
            prev = None
            continue
        if prev is None or prev.numbered != b.numbered or b.list_start:
            counters.clear()
        counters[b.level] = counters.get(b.level, 0) + 1
        for deeper in [k for k in counters if k > b.level]:
            del counters[deeper]
        typ = PRESETS[b.numbered][b.level % 3]
        n = counters[b.level]
        if typ == "decimal":
            lab = str(n)
        elif typ in ("alpha", "ALPHA"):
            lab = chr(96 + (n - 1) % 26 + 1)
            lab = lab.upper() if typ == "ALPHA" else lab
        else:
            lab = int_to_roman(n)
            lab = lab.upper() if typ == "ROMAN" else lab
        labels[id(b)] = lab + "."
        prev = b
    return labels


# --------------------------------------------------------------------------- #
# Slides: every paragraph is a point
# --------------------------------------------------------------------------- #

# Points (pt) a paragraph must be indented past the one above to count as
# "indented under it".
_INDENT_STEP = 4.0
# Indented text this long (characters) is prose that belongs with the point
# above it; shorter lines (formulas) stay on their own line.
_PROSE_CHARS = 60


def bulletise_slide(blocks: list[Block], indent_of, nest_bullets: bool = False) -> list[Block]:
    """Slide text reads as a list of points, so each paragraph becomes a
    bullet. `indent_of(block)` gives a paragraph's left edge, to spot text
    indented under the paragraph above it.

    * Slides with typed numbers ("1.", "(a)") keep their own format: lines
      between the items stay plain text.
    * Slides with bullets of their own ("•"): left alone, unless
      `nest_bullets` (PDF slide decks), where the paragraphs become the main
      points and the slide's bullets nest under the paragraph above them.
    * An all-bold line ("Objectives of third party procedures") leads a
      group: the plain paragraphs after it nest under it, until the next
      paragraph that is bold or starts in bold.
    * A paragraph ending ":" with indented text under it ("NOTE: Section 6(11)
      MCA:" + the quoted subsection) is one point.
    * A paragraph introducing a lettered/roman list ("… any of the following:"
      then (a), (b)) gets a number ("1."), so the letters nest under it.
    * Text after a list item that carries on its sentence ("…P's property and
      affairs," / "when P no longer has capacity…") joins that item.
    Returns the new block list (merged paragraphs are removed)."""
    texts = [b for b in blocks if b.kind in ("paragraph", "bullet") and not b.spacer]
    # (a real list: two or more items, or one that starts a list; a quoted
    # "4. Subject to Rules 5, 8 and 9 …" on its own is just a paragraph)
    items = [marker_of(b.text)[0] for b in blocks if b.kind == "paragraph" and not b.spacer and marker_of(b.text)]
    numbered_slide = len(items) >= 2 or (len(items) == 1 and items[0].lower() in ("1", "a", "i"))
    has_bullets = any(b.kind == "bullet" for b in texts)
    if not texts or (has_bullets and (numbered_slide or not nest_bullets)):
        return blocks
    out: list[Block] = []
    last_item: Block | None = None    # most recent typed list item
    point: Block | None = None        # most recent plain point
    plain_points: set[int] = set()
    shift = 0                         # how far the slide's own bullets move in
    lead = False                      # inside an all-bold line's group
    for b in blocks:
        if b.kind == "bullet" and not b.numbered:
            b.level += shift
            out.append(b)
            last_item = None
            continue
        if b.kind != "paragraph" or b.spacer:
            out.append(b)
            last_item = point = None
            shift, lead = 0, False
            continue
        if numbered_slide and marker_of(b.text):
            out.append(b)
            last_item, point = b, None
            continue
        if (last_item is not None and _carries_on(last_item, b)
                and indent_of(b) <= indent_of(last_item) + _INDENT_STEP):
            _append(last_item, b)
            continue
        # (prose only: a short indented formula like "RP = E(RP) + βP F"
        # stays on its own indented line)
        if (point is not None and point.text.rstrip().endswith(":")
                and indent_of(b) > indent_of(point) + _INDENT_STEP and len(b.text) >= _PROSE_CHARS):
            _append(point, b)
            continue
        if b.level:  # indented under something else: leave it be
            b.level += shift
            out.append(b)
            last_item = None
            continue
        if numbered_slide:
            plain_points.add(id(b))
        elif point is not None and _DASH_START.match(b.text):
            # "- Note that …" typed with a dash: a sub-point of the one above
            b.runs = _strip_marker(b.runs, _DASH_START.match(b.text).group(0))
            b.kind, b.level = "bullet", point.level + 1
            b.note = (b.note + "; " if b.note else "") + "dash line -> sub-bullet"
            out.append(b)
            last_item = None
            continue
        else:
            if _all_bold(b):
                level, lead = 0, True
            elif _starts_bold(b):
                level, lead = 0, False
            else:
                level = 1 if lead else 0
            b.kind, b.level = "bullet", level
            b.note = (b.note + "; " if b.note else "") + "slide paragraph -> bullet"
            shift = level + 1
        out.append(b)
        point, last_item = b, None

    # Number the points that introduce a lettered/roman list
    n = 0
    for i, b in enumerate(out):
        nxt = out[i + 1] if i + 1 < len(out) else None
        m = marker_of(nxt.text) if nxt is not None and nxt.kind == "paragraph" else None
        is_point = (b.kind == "bullet" and not b.numbered) or id(b) in plain_points
        if is_point and m and m[0].lower() in ("a", "i"):
            prev = out[i - 1] if i else None
            continuing = prev is not None and prev.kind == "paragraph" and marker_of(prev.text)
            n = n + 1 if continuing else 1
            b.kind = "paragraph"
            b.level = 0
            b.runs = [Run(f"{n}. ")] + b.runs
            b.note += f"; introduces a list, numbered {n}."
    return out


_DASH_START = re.compile(r"^\s*[-–—]\s+")


def _all_bold(b: Block) -> bool:
    """A short line entirely in bold: the lead-in to the text under it."""
    visible = [r for r in b.runs if r.text.strip()]
    text = b.text.strip()
    return bool(visible) and all(r.bold for r in visible) and len(text) <= 120 and not text.endswith(".")


def _starts_bold(b: Block) -> bool:
    first = next((r for r in b.runs if r.text.strip()), None)
    return first is not None and first.bold


def _carries_on(item: Block, b: Block) -> bool:
    """Does `b` continue the sentence of list item `item`?"""
    prev = item.text.rstrip()
    first = b.text.lstrip()[:1]
    return bool(prev) and (first.islower() or prev[-1] in ",;")


def _append(into: Block, b: Block) -> None:
    into.runs = merge_runs(into.runs + [Run(" ")] + b.runs)
    into.note = (into.note + "; " if into.note else "") + "joined with the following paragraph"
