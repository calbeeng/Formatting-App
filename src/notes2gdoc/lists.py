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
    return (m.group("p") or m.group("d")), m.group(0)


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


def _convert_run(items: list[Block]) -> None:
    """Split a run of marker paragraphs where the numbering starts again (a
    second "1." straight after a list ending "3."), then convert each part."""
    first_tok = marker_of(items[0].text)[0].lower()
    start = 0
    for i in range(1, len(items) + 1):
        if i == len(items) or marker_of(items[i].text)[0].lower() == first_tok:
            if _convert_list(items[start:i]) and start:
                items[start].list_start = True
            start = i


def _convert_list(items: list[Block]) -> bool:
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
    for b, mtext, typ, _rank in parsed:
        b.kind = "bullet"
        b.numbered = preset
        b.level = levels[typ]
        b.runs = _strip_marker(b.runs, mtext)
        b.note = (b.note + "; " if b.note else "") + f"numbered list ({typ})"
    return True


def convert_numbered_lists(doc: Document) -> None:
    _convert_blocks(doc.blocks)


def _is_item(b: Block | None) -> bool:
    return b is not None and b.kind == "paragraph" and not b.spacer and bool(marker_of(b.text))


def _is_inside(b: Block) -> bool:
    """Content that can sit between two items of a list: an ordinary bullet or
    an indented paragraph (a sub-point or note under the item above)."""
    return (b.kind == "bullet" and not b.numbered) or (b.kind == "paragraph" and b.level > 0 and not b.spacer)


def _convert_blocks(blocks: list[Block]) -> None:
    run: list[Block] = []
    for b in blocks + [None]:
        if _is_item(b) or (run and b is not None and _is_inside(b)):
            run.append(b)
            continue
        if run:
            _convert_with_inside(run)
            run = []
        if b is not None and b.kind == "table":
            for cell in b.table.cells:
                _convert_blocks(cell.blocks)


def _convert_with_inside(seq: list[Block]) -> None:
    """Convert the list items in `seq`; content between two items of the same
    converted list stays inside it (indented under the item above it)."""
    items = [b for b in seq if _is_item(b)]
    _convert_run(items)
    last: Block | None = None
    for i, b in enumerate(seq):
        if b in items:
            last = b if b.numbered else None
            continue
        nxt = next((x for x in seq[i + 1:] if x in items), None)
        if last is None or nxt is None or not nxt.numbered or nxt.list_start or nxt.numbered != last.numbered:
            continue
        depth = b.level if b.kind == "bullet" else max(0, b.level - 1)
        b.level = last.level + 1 + depth
        b.kind = "paragraph"
        b.in_list = True
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


def bulletise_slide(blocks: list[Block], indent_of) -> list[Block]:
    """Slide text without bullets of its own reads as a list of points, so make
    each paragraph a bullet. Slides that already have bullets ("•", "1.",
    "(a)") keep their own format. `indent_of(block)` gives a paragraph's left
    edge, to spot text indented under the paragraph above it.

    * A paragraph ending ":" with indented text under it ("NOTE: Section 6(11)
      MCA:" + the quoted subsection) is one point.
    * A paragraph introducing a lettered/roman list ("… any of the following:"
      then (a), (b)) gets a number ("1."), so the letters nest under it.
    * Text after a list item that carries on its sentence ("…P's property and
      affairs," / "when P no longer has capacity…") joins that item.
    Returns the new block list (merged paragraphs are removed)."""
    texts = [b for b in blocks if b.kind in ("paragraph", "bullet") and not b.spacer]
    if not texts or any(b.kind == "bullet" for b in texts):
        return blocks
    out: list[Block] = []
    last_item: Block | None = None    # most recent typed list item
    point: Block | None = None        # most recent plain point
    for b in blocks:
        if b.kind != "paragraph" or b.spacer:
            out.append(b)
            last_item = point = None
            continue
        if marker_of(b.text):
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
            out.append(b)
            last_item = point = None
            continue
        b.kind, b.level = "bullet", 0
        b.note = (b.note + "; " if b.note else "") + "slide paragraph -> bullet"
        out.append(b)
        point, last_item = b, None

    # Number the points that introduce a lettered/roman list
    n = 0
    for i, b in enumerate(out):
        nxt = out[i + 1] if i + 1 < len(out) else None
        m = marker_of(nxt.text) if nxt is not None and nxt.kind == "paragraph" else None
        if b.kind == "bullet" and not b.numbered and m and m[0].lower() in ("a", "i"):
            prev = out[i - 1] if i else None
            continuing = prev is not None and prev.kind == "paragraph" and marker_of(prev.text)
            n = n + 1 if continuing else 1
            b.kind = "paragraph"
            b.runs = [Run(f"{n}. ")] + b.runs
            b.note += f"; introduces a list, numbered {n}."
    return out


def _carries_on(item: Block, b: Block) -> bool:
    """Does `b` continue the sentence of list item `item`?"""
    prev = item.text.rstrip()
    first = b.text.lstrip()[:1]
    return bool(prev) and (first.islower() or prev[-1] in ",;")


def _append(into: Block, b: Block) -> None:
    into.runs = merge_runs(into.runs + [Run(" ")] + b.runs)
    into.note = (into.note + "; " if into.note else "") + "joined with the following paragraph"
