"""Slide-deck PDFs (exported from PowerPoint, Keynote, Google Slides).

Each slide becomes:
* a heading (style key "slide_title") from the title at the top of the slide,
* optionally a sub-heading ("slide_subtitle") for a bold line right under it,
* the current step of a process banner (a row of chevrons where only the
  current step is in dark text) as a "slide_step" heading,
* then its body: bullets keep their nesting levels; tables become tables;
  flowcharts become a picture plus their text as bullets.

Rules:
* Title: the top-most non-bullet text on the slide, plus following lines of the
  same font size (titles often wrap onto 2-3 lines). A title line starting with
  a dash ("- Acquisition of Shares") is part of the title, not a bullet.
* Section divider slides (just 1-3 lines of big text, no bullets, not at the
  top, e.g. "Pre-Acquisition Steps") become "section_title" headings (Heading 1
  by default), so the slide titles that follow sit underneath them.
* Consecutive slides with the same title ("UNCITRAL Model Law" x7) get only one
  heading; the later slides' content carries on under it. The same goes for a
  subtitle repeated on each of those slides.
* Title colour is dropped, because in slides it's theme decoration rather than
  meaning. A title that is entirely bold or italic loses that too, so the target
  doc's heading style decides the look.
* First slide with no bullets = title slide: kept, but unticked by default.
* Boilerplate slides (title "Notice"/"Copyright"/"Disclaimer", or containing "All
  rights reserved") are unticked by default (Settings.skip_boilerplate_slides).
"""

from __future__ import annotations

import re
from dataclasses import replace

from ...config import Settings
from ...lists import bulletise_slide
from ...model import Block, Run, merge_runs
from .extract import DASH_BULLETS, Line
from .layout import cluster_positions, level_for
from .structure import (
    INDENT_TOLERANCE,
    OpenBlock,
    finish_block,
    is_decorative,
    join_line,
    hanging_wrap,
    is_new_item,
    item_text_x,
    region_blocks,
    starts_list_item,
    strip_uniform_style,
)

TITLE_ZONE = 0.30           # titles start in the top 30% of the slide
TITLE_SIZE_TOLERANCE = 1.5  # points; wrapped title lines share a font size
# Slide line spacing is looser than documents, so allow a bit more.
SLIDE_WRAP_SPACING = 1.6
# ...and for lines lined up exactly with a bullet's text, which are almost
# certainly that bullet wrapping, allow looser spacing.
ALIGNED_WRAP_SPACING = 2.2
# Section divider slides: big text (at least this many points), few words.
SECTION_MIN_SIZE = 32
SECTION_MAX_CHARS = 100

BOILERPLATE_TITLES = {"notice", "copyright", "disclaimer", "legal notice", "important notice"}
_BOILERPLATE_TEXT = re.compile(r"all rights reserved", re.I)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().strip(".:").lower()


def _heading(lines: list[Line], style_key: str, page: int, note: str) -> Block:
    """Join lines into one heading, dropping colour and uniform bold/italic."""
    runs: list[Run] = []
    for i, ln in enumerate(lines):
        if i:
            runs.append(Run(" "))
        runs.extend(ln.runs())
    runs = [replace(r, color=None) for r in runs]
    runs = strip_uniform_style(merge_runs(runs), bold=True, italic=True)
    # A fully underlined title is template decoration too
    visible = [r for r in runs if r.text.strip()]
    if visible and all(r.underline for r in visible):
        runs = merge_runs([replace(r, underline=False) for r in runs])
    return finish_block(Block("heading", runs, style_key=style_key, page=page, note=note))


def build_slide_blocks(pages: list[list], settings: Settings) -> list[Block]:
    blocks: list[Block] = []
    prev_title: str | None = None
    prev_title_page = 0
    prev_subtitle: str | None = None
    prev_step: str | None = None

    for idx, items in enumerate(pages):
        items = [
            it for it in items
            if not isinstance(it, Line) or (not it.blank and not is_decorative(it) and it.text.strip())
        ]
        if not items:
            continue
        slide_no = idx + 1
        lines = [it for it in items if isinstance(it, Line)]
        has_regions = len(lines) < len(items)
        # Bullet levels are worked out per slide: each slide's text box can sit
        # at a slightly different position, so deck-wide positions would invent
        # extra levels. Leftmost bullets on the slide = level 0.
        bullet_levels = cluster_positions([ln.level_x for ln in lines if ln.bullet])

        # --- Title slide -----------------------------------------------------
        if idx == 0 and not any(ln.bullet for ln in lines) and not has_regions:
            for ln in lines:
                blocks.append(finish_block(Block(
                    "paragraph", ln.runs(), page=slide_no, selected=False,
                    note="title slide (unticked by default)")))
            continue

        # --- Section divider slide --------------------------------------------
        if _is_section_slide(lines, has_regions):
            blocks.append(_heading(lines, "section_title", slide_no, f"section divider slide {slide_no}"))
            prev_title = None
            continue

        title_lines, body = _split_title(items)
        title_text = " ".join(ln.text.strip() for ln in title_lines)
        boilerplate = _norm(title_text) in BOILERPLATE_TITLES or any(
            _BOILERPLATE_TEXT.search(ln.text) for ln in lines
        )
        slide_blocks: list[Block] = []
        merged_note = ""

        # --- Title -------------------------------------------------------------
        if title_lines:
            if _norm(title_text) == prev_title and not boilerplate:
                merged_note = f"slide {slide_no}: same title as slide {prev_title_page}, merged"
            else:
                slide_blocks.append(_heading(title_lines, "slide_title", slide_no, f"slide {slide_no} title"))
                prev_title = _norm(title_text)
                prev_title_page = slide_no

        # --- Subtitle: bold, non-bullet line(s) straight after the title -------
        if title_lines and body and isinstance(body[0], Line) and _is_subtitle(body[0]):
            sub = [body.pop(0)]
            while body and isinstance(body[0], Line) and not body[0].bullet \
                    and body[0].bold_ratio >= 0.6 and _wraps_line(sub[-1], body[0]):
                sub.append(body.pop(0))
            sub_text = _norm(" ".join(ln.text for ln in sub))
            # Repeated on every slide of a merged run ("BEST INTERESTS" /
            # "Section 6, MCA" x5): only the first one is a heading
            if not (merged_note and sub_text == prev_subtitle):
                slide_blocks.append(_heading(sub, "slide_subtitle", slide_no, "bold line under the slide title"))
            prev_subtitle = sub_text
        elif not merged_note:
            prev_subtitle = None

        # --- Body ------------------------------------------------------------
        open_: OpenBlock | None = None
        # The most recent bullet's (glyph x, level), for paragraphs that sit
        # inside a list, like an indented quote under a bullet.
        last_bullet: tuple[float, int] | None = None
        first_x: dict[int, float] = {}   # paragraph -> left edge of its first line

        def close():
            nonlocal open_
            if open_:
                finish_block(open_.block)
            open_ = None

        # The current step of a process banner ("4. Consents / Service on
        # Relevant Persons"): a small heading, once per run of slides on it.
        step = [it for it in body if isinstance(it, Line) and it.banner]
        if step:
            body = [it for it in body if not (isinstance(it, Line) and it.banner)]
            step_text = _norm(" ".join(ln.text for ln in step))
            if step_text != prev_step:
                slide_blocks.append(_heading(step, "slide_step", slide_no, "current step in a process banner"))
            prev_step = step_text
        elif not merged_note:
            prev_step = None

        for it in body:
            if not isinstance(it, Line):
                close()
                slide_blocks.extend(region_blocks(it, slide_no))
                last_bullet = None
                continue
            ln = it
            # "– 2101]" style continuation, not a dash bullet
            if ln.bullet in DASH_BULLETS and open_ and _wraps(open_, ln):
                ln.pieces, ln.bullet = ln.pieces_with_glyph, None
            if ln.bullet:
                close()
                level = level_for(ln.level_x, bullet_levels)
                b = Block("bullet", ln.runs(), level=level, page=slide_no,
                          note=f"bullet at x={ln.x0:.0f} -> level {level}")
                slide_blocks.append(b)
                open_ = OpenBlock(b, ln.text_x0, ln, [ln])
                last_bullet = (ln.level_x, level)
                continue
            if open_ and _wraps(open_, ln):
                join_line(open_.block, ln)
                open_.last = ln
                open_.lines.append(ln)
                continue
            close()
            b = Block("paragraph", ln.runs(), page=slide_no)
            first_x[id(b)] = ln.x0
            if last_bullet and ln.level_x > last_bullet[0] + INDENT_TOLERANCE:
                # Indented past the bullet above it: it's part of that list
                # item (e.g. a quoted clause), so indent it to the bullet's text.
                b.level = last_bullet[1] + 1
                b.note = f"paragraph inside a list, indent level {b.level}"
            slide_blocks.append(b)
            open_ = OpenBlock(b, item_text_x(ln), ln, [ln])
        close()
        if not boilerplate:
            slide_blocks = bulletise_slide(slide_blocks, lambda b: first_x.get(id(b), 0.0))

        if merged_note and slide_blocks:
            slide_blocks[0].note = merged_note + (f"; {slide_blocks[0].note}" if slide_blocks[0].note else "")
        if boilerplate:
            for b in slide_blocks:
                b.selected = not settings.skip_boilerplate_slides
                b.note = f"boilerplate slide {slide_no}" + (f"; {b.note}" if b.note else "")
            prev_title = None  # don't let later slides merge into a boilerplate title
        blocks.extend(slide_blocks)
    return blocks


def _is_section_slide(lines: list[Line], has_regions: bool) -> bool:
    if has_regions or not lines or len(lines) > 3 or any(ln.bullet for ln in lines):
        return False
    text = " ".join(ln.text.strip() for ln in lines)
    return (
        len(text) <= SECTION_MAX_CHARS
        and all(ln.size >= SECTION_MIN_SIZE for ln in lines)
        and lines[0].y0 > lines[0].page_height * 0.25
    )


def _is_subtitle(ln: Line) -> bool:
    # Not in a column: a bold line atop one column is that column's heading
    return not ln.bullet and ln.column == 0 and ln.bold_ratio >= 0.6 and len(ln.text.strip()) <= 120


def _split_title(items: list) -> tuple[list[Line], list]:
    first = items[0]
    if not isinstance(first, Line) or first.bullet or first.y0 > first.page_height * TITLE_ZONE:
        return [], items
    title = [first]
    for it in items[1:]:
        if not isinstance(it, Line):
            break
        prev = title[-1]
        same_size = abs(it.size - first.size) <= TITLE_SIZE_TOLERANCE
        if same_size and it.bullet in DASH_BULLETS:
            # "- Acquisition of Shares" under "Methods of Acquisition": the dash
            # belongs to the title text, so put it back.
            it.pieces, it.bullet = it.pieces_with_glyph, None
        if not it.bullet and same_size and 0 < it.y0 - prev.y0 <= SLIDE_WRAP_SPACING * it.size:
            title.append(it)
        else:
            break
    return title, items[len(title):]


def _wraps_line(prev: Line, ln: Line) -> bool:
    return 0 < ln.y0 - prev.y0 <= SLIDE_WRAP_SPACING * max(ln.size, prev.size)


def _wraps(open_: OpenBlock, ln: Line) -> bool:
    if is_new_item(open_, ln):
        return False
    if hanging_wrap(open_, ln):
        return True
    aligned = abs(ln.x0 - open_.text_x) <= INDENT_TOLERANCE + 1
    if aligned and open_.block.kind == "bullet" and not ln.bullet:
        # Lined up exactly with the bullet's text: a wrapped line, even when the
        # deck uses loose line spacing (up to ALIGNED_WRAP_SPACING x font size).
        dy = ln.y0 - open_.last.y0
        return 0 < dy <= ALIGNED_WRAP_SPACING * max(ln.size, open_.last.size)
    if not _wraps_line(open_.last, ln):
        return False
    if aligned:
        return True
    # A bullet's wrapped lines sometimes start a little left or right of its
    # first line's text. Accept any same-size line indented past the bullet
    # glyph. (Different-size text, like a smaller quote under a bullet, stays
    # a separate paragraph.)
    first = open_.lines[0]
    return (
        open_.block.kind == "bullet"
        and ln.x0 > first.x0 + 2
        and abs(ln.size - first.size) <= TITLE_SIZE_TOLERANCE
    )
