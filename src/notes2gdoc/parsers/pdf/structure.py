"""Turn visual lines into Blocks (headings, bullets, paragraphs) for ordinary
(portrait, document-style) PDFs. Slide decks use slides.py instead, but share
the line-joining helpers here.

Decisions, in order, for each line:

1. Blank line            -> ends the current block (Word writes empty
                            paragraphs as blank lines, so they're a reliable
                            "new paragraph" signal).
2. Decorative line       -> dropped ("* * * * *", "-----").
3. Bold + numbered       -> heading ("1.", "(a)", "(ii)"). Inside a drawn box,
                            numbered lines are left as paragraphs.
4. Starts with a glyph   -> bullet. Nesting level comes from the glyph's
                            x-position, clustered across the whole document.
5. Continuation?         -> appended to the open block (wrapped lines, and
                            text that continues on the next page).
6. Otherwise             -> new paragraph.

Title block: centred lines on page 1, before the first heading or bullet, are
kept as paragraphs but unticked by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

import pymupdf as fitz

from ...model import Block, Image, Run, Table, TableCell, merge_runs, normalise_whitespace
from ...numbering import HeadingCandidate, classify_paren_markers, match_marker
from .extract import DASH_BULLETS, Line
from .layout import cluster_positions, level_for
from .regions import DiagramRegion, TableRegion

# How far (points) a wrapped line's left edge may be from the block's text edge.
INDENT_TOLERANCE = 3.5
# Max distance between consecutive line tops, as a multiple of font size, for
# the second line to count as a wrap of the first. Word body text at 11pt wraps
# at ~13.5pt (1.23x); a new item with paragraph spacing is ~20pt (1.8x).
WRAP_SPACING = 1.5
# A line at the top of a page continues the previous page's open block if it
# starts within this many font-sizes of the page's normal first-line position.
PAGE_TOP_SLACK = 0.6

_DECORATIVE = re.compile(r"^[\s\*\-–—_=~•·.]+$")

# A line ending in a hyphen ("decision-" / "specific", or "insol-" / "vency"):
# is the hyphen part of the word, or was the word broken across the lines?
# While joining lines we can't tell yet, so a LINE_HYPHEN marker goes in its
# place. Once the whole document is read, resolve_line_hyphens() drops the
# hyphen if the joined word ("insolvency") appears elsewhere in the document,
# and otherwise keeps it ("decision-specific"). Words ending in these prefixes
# always keep it ("re-possess").
KEEP_HYPHEN_PREFIXES = {"re", "co", "pre", "non", "self", "ex", "quasi", "cross", "post", "anti", "sub"}
LINE_HYPHEN = "\u00ad"  # soft hyphen; never left in the final text


@dataclass
class OpenBlock:
    """The block currently being built, plus the geometry needed to decide
    whether the next line continues it."""

    block: Block
    text_x: float
    last: Line
    lines: list[Line] = field(default_factory=list)


def join_line(block: Block, line: Line) -> None:
    """Append a wrapped line's text to a block, fixing line-end hyphenation."""
    runs = line.runs()
    if not runs:
        return
    prev_text = block.text.rstrip()
    next_text = "".join(r.text for r in runs).lstrip()
    m = re.search(r"([A-Za-z]+)-$", prev_text)
    if m and next_text[:1].islower():
        # Word broken across lines
        _trim_trailing_space(block)
        if m.group(1).lower() not in KEEP_HYPHEN_PREFIXES:
            _drop_last_char(block)  # decided later by resolve_line_hyphens
            block.runs.append(replace(block.runs[-1], text=LINE_HYPHEN) if block.runs else Run(LINE_HYPHEN))
        block.runs = merge_runs(block.runs + _lstrip_runs(runs))
        return
    if not block.text.endswith(" "):
        block.runs.append(Run(" "))
    block.runs = merge_runs(block.runs + runs)


_WORD = re.compile(r"[A-Za-z]+")
_BROKEN = re.compile(r"([A-Za-z]+)" + LINE_HYPHEN + r"([A-Za-z]+)")


def _all_blocks(blocks: list[Block]):
    for b in blocks:
        yield b
        if b.table is not None:
            for cell in b.table.cells:
                yield from _all_blocks(cell.blocks)


def resolve_line_hyphens(blocks: list[Block]) -> None:
    """Replace each LINE_HYPHEN marker: nothing if the joined word is used
    elsewhere in the document ("insol-" + "vency" -> "insolvency"), else a
    real hyphen ("decision-" + "specific" -> "decision-specific")."""
    every = list(_all_blocks(blocks))
    vocab = {w.lower() for b in every for w in _WORD.findall(b.text.replace(LINE_HYPHEN, "-"))}
    for b in every:
        text = b.text
        if LINE_HYPHEN not in text:
            continue
        # Decide each marker in order, then apply the decisions run by run
        keep = []
        for i, ch in enumerate(text):
            if ch == LINE_HYPHEN:
                left = re.search(r"[A-Za-z]+$", text[:i])
                right = re.match(r"[A-Za-z]+", text[i + 1:])
                joined = (left.group(0) if left else "") + (right.group(0) if right else "")
                keep.append(joined.lower() not in vocab)
        decisions = iter(keep)
        b.runs = merge_runs([
            replace(r, text="".join(("-" if next(decisions) else "") if ch == LINE_HYPHEN else ch for ch in r.text))
            for r in b.runs
        ])


def _trim_trailing_space(block: Block) -> None:
    while block.runs and block.runs[-1].text.endswith(" "):
        t = block.runs[-1].text.rstrip(" ")
        if t:
            block.runs[-1] = replace(block.runs[-1], text=t)
        else:
            block.runs.pop()


def _drop_last_char(block: Block) -> None:
    last = block.runs[-1]
    if len(last.text) > 1:
        block.runs[-1] = replace(last, text=last.text[:-1])
    else:
        block.runs.pop()


def _lstrip_runs(runs: list[Run]) -> list[Run]:
    runs = list(runs)
    while runs and not runs[0].text.strip():
        runs.pop(0)
    if runs:
        runs[0] = replace(runs[0], text=runs[0].text.lstrip())
    return runs


def strip_uniform_style(runs: list[Run], bold: bool = True, italic: bool = False) -> list[Run]:
    """If *every* visible character of a heading is bold (or italic), drop that
    attribute: it's the heading's look, which the target doc's heading style
    should supply. Partial bold/italic (e.g. a case name) is kept."""
    visible = [r for r in runs if r.text.strip()]
    out = runs
    if bold and visible and all(r.bold for r in visible):
        out = [replace(r, bold=False) for r in out]
    if italic and visible and all(r.italic for r in visible):
        out = [replace(r, italic=False) for r in out]
    return merge_runs(out)


def finish_block(block: Block) -> Block:
    block.runs = normalise_whitespace(block.runs)
    return block


def is_decorative(line: Line) -> bool:
    return bool(_DECORATIVE.match(line.text)) and not line.bullet


# A typed list marker at the start of a line: "a.", "b)", "(c)", "1.", "2)".
# (letters: one letter, or a roman numeral like "iv"; not "MR." or "Dr.")
_LIST_MARKER = re.compile(r"^\s*(\((?:\d{1,3}|[a-zA-Z]|[ivxlcIVXLC]{1,4})\)|(?:\d{1,3}|[a-zA-Z]|[ivxlcIVXLC]{1,4})[.)])\s")


def starts_list_item(line: Line) -> bool:
    """Lines like "a. Adultery" always start a new item, even when they sit
    right under the previous line."""
    return bool(_LIST_MARKER.match(line.text))


def item_text_x(line: Line) -> float:
    """Where wrapped lines of this item will start: after the list marker
    ("a.   Adultery" -> x of "Adultery"), otherwise the line's left edge."""
    if not starts_list_item(line):
        return line.x0
    # The second word on the line is the first word after the marker
    starts = [x for x in line.word_starts if x >= line.x0 - 1]
    if len(starts) >= 2:
        return starts[1]
    marker = _LIST_MARKER.match(line.text).group(1)
    return line.x0 + (len(marker) + 1) * 0.5 * line.size  # estimate


_SENTENCE_END = ".:;!?-–—"


def is_new_item(open_: OpenBlock, line: Line) -> bool:
    """Does this line start a new list item?

    A line starting with a marker like "(1)" or "a." normally does, except in
    the middle of an ordinary paragraph: in "…requirements of subsections" /
    "(1) to (9)) the person…" the "(1)" just happens to start the next line.
    That's recognised when the paragraph isn't itself a list item, the line
    above doesn't end a sentence or clause, the line lines up with it in the
    same font size, and the text after the "number" doesn't start with a
    capital ("1. When donor…" under "Section 15(2), MCA" is a real item)."""
    if not starts_list_item(line):
        return False
    prev = open_.last.text.rstrip()
    after = _LIST_MARKER.sub("", line.text, count=1).lstrip()
    mid_sentence = (
        not after[:1].isupper()
        and not starts_list_item(open_.lines[0])
        and bool(prev) and prev[-1] not in _SENTENCE_END
        and abs(line.x0 - open_.last.x0) <= INDENT_TOLERANCE
        and abs(line.size - open_.last.size) <= 1
    )
    return not mid_sentence


def hanging_wrap(open_: OpenBlock, line: Line) -> bool:
    """A wrapped line of a lettered item lines up with the text after the
    marker ("e. DMA ... from 1" / "July 2024") or with the marker itself
    ("(a) P's personal welfare … concerning P's" / "personal welfare;"). That
    alignment is a strong enough signal to allow looser line spacing (up to
    3x the font size)."""
    if not starts_list_item(open_.lines[0]) or open_.block.kind != "paragraph":
        return False
    dy = line.y0 - open_.last.y0
    aligned = (abs(line.x0 - open_.text_x) <= INDENT_TOLERANCE
               or abs(line.x0 - open_.lines[0].x0) <= INDENT_TOLERANCE)
    return 0 < dy <= 3 * line.size and aligned


def wraps_onto(open_: OpenBlock, line: Line) -> bool:
    """Same-page continuation test: close below, at the block's text indent."""
    if line.in_box != open_.last.in_box or is_new_item(open_, line):
        return False
    if hanging_wrap(open_, line):
        return True
    dy = line.y0 - open_.last.y0
    if dy <= 0 or dy > WRAP_SPACING * max(line.size, open_.last.size):
        return False
    if abs(line.x0 - open_.text_x) <= INDENT_TOLERANCE:
        return True
    # Paragraph with a first-line indent: the 2nd line starts further left.
    # (Not for centred lines like a title block, where each line stands alone.)
    if (
        open_.block.kind == "paragraph"
        and len(open_.lines) == 1
        and line.x0 < open_.text_x
        and not _is_centred(open_.last)
    ):
        return True
    return False


def ends_paragraph(prev: Line, right_edge: float) -> bool:
    """A short line ending in sentence punctuation ends its paragraph, even
    without extra spacing below it."""
    text = prev.text.rstrip()
    return bool(text) and text[-1] in ".?!:" and prev.x1 < right_edge - 0.15 * (right_edge - prev.text_x0)


def build_document_blocks(pages: list[list]) -> list[Block]:
    """`pages` holds, per page, Lines plus TableRegion/DiagramRegion items in
    reading order (see extract.extract_page)."""
    all_lines = [ln for page in pages for ln in page if isinstance(ln, Line)]
    bullet_levels = cluster_positions([ln.level_x for ln in all_lines if ln.bullet])
    right_edge = _percentile([ln.x1 for ln in all_lines if not ln.blank], 0.9)
    body_top = _body_top(pages)

    blocks: list[Block] = []
    candidates: list[tuple[Block, HeadingCandidate]] = []
    open_: OpenBlock | None = None
    seen_structure = False
    last_letter: str | None = None        # last "A." / "B." section heading
    was_bold: dict[int, bool] = {}        # numbered heading -> all bold in the source
    tails: dict[int, Block] = {}          # numbered heading -> the plain text wrapped under it
    tail_of: Block | None = None

    def close():
        nonlocal open_
        if open_:
            finish_block(open_.block)
        open_ = None

    for page_idx, lines in enumerate(pages):
        first_on_page = True
        for line in lines:
            if not isinstance(line, Line):  # a table or diagram
                close()
                blocks.extend(region_blocks(line, page_idx + 1))
                first_on_page = False
                seen_structure = True
                continue
            if line.blank:
                close()
                continue
            if is_decorative(line):
                continue
            text = line.text.strip()
            if not text:
                continue

            # --- Continuation from the previous page --------------------------
            if first_on_page:
                first_on_page = False
                if (
                    open_
                    and open_.last.page < line.page
                    and open_.block.kind in ("bullet", "paragraph")
                    and not line.bullet
                    and not _is_heading_line(line)
                    and abs(line.x0 - open_.text_x) <= INDENT_TOLERANCE
                    and line.y0 - body_top <= PAGE_TOP_SLACK * line.size
                    and (not re.search(r"[.;!?]$", open_.block.text.rstrip()) or text[:1].islower())
                ):
                    join_line(open_.block, line)
                    open_.block.note += f"; continues onto page {line.page + 1}"
                    open_.last = line
                    open_.lines.append(line)
                    continue
                close()  # never continue a heading across pages

            # --- "– 2101]": a dash at the text indent of the open block is a
            # wrapped continuation, not a dash bullet. Put the dash back.
            if line.bullet in DASH_BULLETS and open_ and wraps_onto(open_, line):
                line.pieces = line.pieces_with_glyph
                line.bullet = None

            # --- A section heading wrapping onto a second (often centred) line --
            if (open_ and open_.block.kind == "heading" and open_.block.label is None
                    and open_.last.page == line.page and not line.bullet and line.bold_ratio >= 0.9
                    and 0 < line.y0 - open_.last.y0 <= WRAP_SPACING * max(line.size, open_.last.size)
                    and abs(line.size - open_.last.size) <= 1
                    and not _SECTION.match(text) and not match_marker(text)):
                _join_heading(open_.block, strip_uniform_style(line.runs(), bold=True, italic=True))
                open_.last = line
                open_.lines.append(line)
                continue

            # --- Section heading: "I. INTRODUCTION", "A. Sources of …" ---------
            section = _section_heading(line, text, last_letter)
            if section:
                close()
                key, last_letter = section
                block = Block("heading", strip_uniform_style(line.runs(), bold=True, italic=True),
                              style_key=key, page=page_idx + 1,
                              note="bold section line (roman numeral)" if key == "decimal" else "bold section line (letter)")
                blocks.append(block)
                open_ = OpenBlock(block, line.x0, line, [line])
                seen_structure = True
                continue

            # --- Heading -------------------------------------------------------
            marker = match_marker(text)
            if marker and _is_heading_line(line):
                close()
                runs = strip_uniform_style(line.runs())
                block = Block("heading", runs, label=marker.label, page=page_idx + 1)
                blocks.append(block)
                candidates.append((block, HeadingCandidate(marker, x=line.x0)))
                was_bold[id(block)] = all(r.bold for r in line.runs() if r.text.strip())
                open_ = OpenBlock(block, _text_after_label_x(line), line, [line])
                seen_structure = True
                continue

            # --- Bullet --------------------------------------------------------
            if line.bullet:
                close()
                level = level_for(line.level_x, bullet_levels)
                block = Block(
                    "bullet", line.runs(), level=level, page=page_idx + 1,
                    note=f"bullet '{line.bullet}' at x={line.x0:.0f} -> level {level}",
                )
                blocks.append(block)
                open_ = OpenBlock(block, line.text_x0, line, [line])
                seen_structure = True
                continue

            # --- Continuation on the same page ---------------------------------
            if open_ and wraps_onto(open_, line):
                kind = open_.block.kind
                # A wrapped heading line must also be bold; plain text right
                # under a heading is a new paragraph.
                heading_ok = kind != "heading" or line.bold_ratio >= 0.6
                para_break = kind == "paragraph" and ends_paragraph(open_.last, right_edge)
                if heading_ok and not para_break:
                    if kind == "heading":
                        _join_heading(open_.block, strip_uniform_style(line.runs()))
                    else:
                        join_line(open_.block, line)
                    open_.last = line
                    open_.lines.append(line)
                    continue
                if kind == "heading" and not heading_ok and id(open_.block) in was_bold:
                    tail_of = open_.block  # wrapped text of a numbered "heading"; see _demote_list_items

            # --- New paragraph -------------------------------------------------
            close()
            block = Block("paragraph", line.runs(), page=page_idx + 1)
            if tail_of is not None:
                tails[id(tail_of)] = block
                tail_of = None
            if line.in_box:
                block.note = "inside a bordered box"
            if not seen_structure and page_idx == 0 and _is_centred(line):
                block.selected = False
                block.note = "title block (unticked by default)"
            blocks.append(block)
            open_ = OpenBlock(block, item_text_x(line), line, [line])
    close()

    candidates = _demote_list_items(blocks, candidates, was_bold, tails)

    # Decide alpha vs roman for "(i)"-style markers, now that we can look ahead.
    cands = [c for _, c in candidates]
    classify_paren_markers(cands)
    for block, cand in candidates:
        block.style_key = cand.style_key
        block.note = cand.reason + (f"; {block.note}" if block.note else "")
    return blocks


# --------------------------------------------------------------------------- #
# Tables and diagrams
# --------------------------------------------------------------------------- #

def flow_blocks(lines: list[Line], page_no: int) -> list[Block]:
    """Simple paragraph/bullet builder for small areas: table cells and
    diagram boxes. Text there is often centred, so any non-bullet line close
    below a paragraph continues it, whatever its x-position."""
    levels = cluster_positions([ln.x0 for ln in lines if ln.bullet])
    blocks: list[Block] = []
    open_: OpenBlock | None = None
    for ln in lines:
        if not ln.text.strip() or is_decorative(ln):
            continue
        if ln.bullet and not (ln.bullet in DASH_BULLETS and open_ and _close_below(open_, ln)
                              and abs(ln.x0 - open_.text_x) <= INDENT_TOLERANCE):
            if open_:
                finish_block(open_.block)
            b = Block("bullet", ln.runs(), level=level_for(ln.x0, levels), page=page_no)
            blocks.append(b)
            open_ = OpenBlock(b, ln.text_x0, ln, [ln])
            continue
        if ln.bullet:  # a dash that's really a wrapped continuation
            ln.pieces, ln.bullet = ln.pieces_with_glyph, None
        # Continues the open item if it's close below and either the item is a
        # paragraph (often centred in cells) or the line is indented past the
        # bullet glyph (wrapped bullet text; its indent can differ from the
        # first line's).
        if open_ and (_close_below(open_, ln) or hanging_wrap(open_, ln)) and not is_new_item(open_, ln) and (
            open_.block.kind == "paragraph" or ln.x0 > open_.lines[0].x0 + 2
        ):
            join_line(open_.block, ln)
            open_.last = ln
            open_.lines.append(ln)
            continue
        if open_:
            finish_block(open_.block)
        b = Block("paragraph", ln.runs(), page=page_no)
        blocks.append(b)
        open_ = OpenBlock(b, item_text_x(ln), ln, [ln])
    if open_:
        finish_block(open_.block)
    return blocks


def _close_below(open_: OpenBlock, ln: Line) -> bool:
    dy = ln.y0 - open_.last.y0
    return 0 < dy <= WRAP_SPACING * max(ln.size, open_.last.size)


def table_block(region: TableRegion, page_no: int) -> Block:
    table = Table(region.n_rows, region.n_cols, col_widths=list(region.col_widths))
    for spec in sorted(region.cells, key=lambda c: (c.row, c.col)):
        table.cells.append(
            TableCell(spec.row, spec.col, flow_blocks(spec.lines, page_no),
                      rowspan=spec.rowspan, colspan=spec.colspan)
        )
    return Block("table", table=table, page=page_no,
                 note=f"table {region.n_rows} rows x {region.n_cols} columns")


# Also list a diagram's box text as bullets under its picture? Off by choice:
# the picture alone is enough. Set to True to bring the text back.
DIAGRAM_TEXT_BELOW = False


def diagram_blocks(region: DiagramRegion, page_no: int) -> list[Block]:
    """The diagram as a picture. Its text is kept as the picture's alt text
    (useful for screen readers / searching).

    With DIAGRAM_TEXT_BELOW on, the text also follows as bullets: each box's
    first paragraph is a top-level bullet and anything else in the box is
    nested under it. Boxes are ordered row by row, left to right."""
    w, h = region.rect.width, region.rect.height
    texts = [ln.text.strip() for ln in region.lines if ln.text.strip()]
    alt = "Diagram: " + "; ".join(texts)[:1000]
    out = [Block("image", image=Image(region.png, w, h, alt), page=page_no,
                 note=f"diagram picture {w:.0f}x{h:.0f}pt")]
    if not DIAGRAM_TEXT_BELOW:
        return out

    # Labels outside boxes (e.g. "Pre:" / "100%" next to an arrow): stack
    # lines that sit directly under each other into one label.
    labels: list[list[Line]] = []
    for ln in sorted(region.labels, key=lambda l: (l.y0, l.x0)):
        for g in labels:
            last = g[-1]
            if 0 < ln.y0 - last.y0 <= WRAP_SPACING * ln.size and abs(ln.x0 - last.x0) <= 20:
                g.append(ln)
                break
        else:
            labels.append([ln])

    # (top, left, lines) for every box and label. Boxes are placed by the box
    # itself, not its text, since text in boxes is often centred.
    placed = [(rect.y0, rect.x0, list(lines)) for rect, lines in region.groups]
    placed += [(min(l.y0 for l in g), min(l.x0 for l in g), g) for g in labels]
    # Row by row (tops within 20pt count as one row), then left to right
    placed.sort(key=lambda p: (round(p[0] / 20), p[1]))
    for _, _, members in placed:
        members.sort(key=lambda l: (l.y0, l.x0))
        inner = flow_blocks(members, page_no)
        for i, b in enumerate(inner):
            b.kind = "bullet"
            b.level = 0 if i == 0 else 1 + min(b.level, 1)
            b.note = "diagram text"
        out.extend(inner)
    return out


def region_blocks(region, page_no: int) -> list[Block]:
    if isinstance(region, TableRegion):
        return [table_block(region, page_no)]
    return diagram_blocks(region, page_no)


def _join_heading(block: Block, runs: list[Run]) -> None:
    if not block.text.endswith(" "):
        block.runs.append(Run(" "))
    block.runs = merge_runs(block.runs + runs)


_SECTION = re.compile(r"^([IVXLC]{1,6}|[A-Z])\.\s+\S")
_ROMAN = re.compile(r"^(X{0,3})(IX|IV|V?I{0,3})$")


def _section_heading(line: Line, text: str, last_letter: str | None) -> tuple[str, str | None] | None:
    """An all-bold line starting "I." / "II." (roman numeral) or "A." / "B."
    (letter): a section heading of an outline. Returns (style key, last
    letter seen). Roman numerals map to the top heading level, letters to the
    next. "I." counts as the letter only straight after "H."."""
    m = _SECTION.match(text)
    if not m or line.bullet or line.in_box or line.bold_ratio < 0.9:
        return None
    tok = m.group(1)
    is_next_letter = len(tok) == 1 and last_letter is not None and ord(tok) == ord(last_letter) + 1
    if _ROMAN.match(tok) and not is_next_letter:
        return "decimal", None      # a new part: its letters start again
    if len(tok) == 1:
        return "alpha", tok
    return None


def _demote_list_items(blocks, candidates, was_bold, tails):
    """A numbered line in bold isn't a heading when it's one item of a list
    whose other items are ordinary text:

        2. **Appellate Division**: see section 3 …
        3. **General Division of the High Court**:        <- all bold, still item 3
        4. **District Court**: see sections 2 …

    Items are grouped into runs that count up (1, 2, 3 … or a, b, c …); a run
    with any ordinary paragraph in it is a list, so its bold lines become
    paragraphs too (keeping their bold). `tails`: the plain text that wrapped
    under such a line. Returns the remaining candidates."""
    from ...lists import _classify, marker_of

    heading_ids = {id(b) for b, _ in candidates}
    runs: dict[str, list[Block]] = {}
    last: dict[str, int] = {}
    demote: set[int] = set()

    def flush(typ: str) -> None:
        run = runs.pop(typ, [])
        if any(id(b) in heading_ids for b in run) and any(id(b) not in heading_ids for b in run):
            demote.update(id(b) for b in run if id(b) in heading_ids)

    for b in blocks:
        if b.kind not in ("heading", "paragraph") or (b.kind == "heading" and id(b) not in heading_ids):
            continue
        m = marker_of(b.text)
        if not m:
            continue
        typed = _classify(m[0], last)
        if typed is None:
            continue
        typ, val = typed
        if typ in runs and val != last.get(typ, 0) + 1:
            flush(typ)
        runs.setdefault(typ, []).append(b)
        last[typ] = val
    for typ in list(runs):
        flush(typ)

    for b, _ in candidates:
        if id(b) in demote:
            b.kind, b.label = "paragraph", None
            if was_bold.get(id(b)):
                b.runs = [replace(r, bold=True) for r in b.runs]
            b.note = "bold numbered line inside a list of ordinary items: kept as a list item"
            # The rest of its sentence was split off (plain text under a
            # heading starts a new paragraph); as a list item it's one paragraph
            tail = tails.get(id(b))
            if tail is not None and any(t is tail for t in blocks):
                b.runs = normalise_whitespace(merge_runs(b.runs + [Run(" ")] + tail.runs))
                blocks[:] = [t for t in blocks if t is not tail]
    return [(b, c) for b, c in candidates if id(b) not in demote]


def _is_heading_line(line: Line) -> bool:
    """A numbered line is a heading if it's mostly bold and not inside a box.

    Non-bold numbered lines (e.g. "(a) the company is unable to pay...") stay as
    ordinary paragraphs, with the numbering kept as text.
    """
    return (
        not line.in_box
        and not line.bullet
        and match_marker(line.text.strip()) is not None
        and line.bold_ratio >= 0.6
    )


def _text_after_label_x(line: Line) -> float:
    """x-position where a heading's text starts after its label, for wrapped
    heading lines (e.g. "(a)" at 72pt, text at 93pt)."""
    visible = [p for p in line.pieces if p.text.strip()]
    marker = match_marker(line.text.strip())
    if len(visible) >= 2 and marker and visible[0].text.strip() == marker.label:
        return visible[1].vis_x0 if visible[1].vis_x0 is not None else visible[1].x0
    return line.x0


def _is_centred(line: Line) -> bool:
    centre = (line.x0 + line.x1) / 2
    return abs(centre - line.page_width / 2) < 0.06 * line.page_width and line.x0 > line.page_width * 0.2


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def _body_top(pages: list[list[Line]]) -> float:
    """Where body text normally starts on a page (ignoring page 1, which often
    has a title). Used to tell "continues from the previous page" apart from
    "new item after a gap at the top of the page"."""
    def first_y(lines):
        return next((ln.y0 for ln in lines if isinstance(ln, Line) and not ln.blank), None)

    tops = [first_y(lines) for lines in (pages[1:] or pages)]
    tops = [t for t in tops if t is not None]
    return min(tops) if tops else 0.0
