"""The intermediate document model.

Every parser (PDF now, Word and PowerPoint later) produces a `Document`, and
everything downstream (the outline printer, the GUI preview and the Google Docs
writer) only ever reads a `Document`. That keeps the messy, format-specific
heuristics inside the parsers.

A Document is a flat list of Blocks. Each Block becomes one paragraph in the
Google Doc:

* kind="heading"   -> a named heading style. Which one (Heading 1/2/3) is decided
                      by `Block.style_key` plus the user's heading map in Settings,
                      so the user can remap levels without re-parsing.
* kind="bullet"    -> a bulleted paragraph, nested `level` deep (0 = top level).
* kind="paragraph" -> NORMAL_TEXT.
* kind="table"     -> a real table; each cell holds its own paragraphs/bullets.
* kind="image"     -> a picture (used for flowcharts/diagrams, which Google
                      Docs can't build as editable drawings).

The text of a block is a list of Runs, where each run has uniform inline styling.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

BlockKind = Literal["heading", "bullet", "paragraph", "table", "image"]


@dataclass
class Run:
    """A stretch of text with uniform inline styling."""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    superscript: bool = False
    subscript: bool = False
    # "#RRGGBB", or None for the default (black) text colour.
    color: str | None = None
    # Highlight ("#RRGGBB" background behind the text), e.g. from Word
    highlight: str | None = None

    def style_key(self) -> tuple:
        return (self.bold, self.italic, self.underline, self.superscript, self.subscript, self.color,
                self.highlight)

    def same_style(self, other: "Run") -> bool:
        return self.style_key() == other.style_key()


@dataclass
class TableCell:
    row: int
    col: int
    blocks: list["Block"] = field(default_factory=list)  # paragraphs/bullets inside the cell
    rowspan: int = 1
    colspan: int = 1
    background: str | None = None  # "#RRGGBB" cell shading

    @property
    def text(self) -> str:
        return " / ".join(b.text for b in self.blocks)


@dataclass
class Table:
    n_rows: int
    n_cols: int
    cells: list[TableCell] = field(default_factory=list)  # merged-away cells are simply absent
    # Relative column widths from the source (e.g. [1, 1, 2]); empty = equal widths
    col_widths: list[float] = field(default_factory=list)

    def cell(self, row: int, col: int) -> TableCell | None:
        return next((c for c in self.cells if c.row == row and c.col == col), None)


@dataclass
class Image:
    png: bytes
    width_pt: float   # size on the source page, in points
    height_pt: float
    alt: str = ""


@dataclass
class Block:
    kind: BlockKind
    runs: list[Run] = field(default_factory=list)
    # Bullets: nesting depth (0 = top level). Paragraphs: how many list levels
    # to indent (e.g. 1 = lined up with the text of a top-level bullet, for a
    # quote sitting inside a list). Unused for headings.
    level: int = 0
    # For headings: which numbering scheme it came from, e.g. "decimal" for "1.",
    # "alpha" for "(a)", "roman" for "(i)", "slide_title". Mapped to a named
    # style through Settings.heading_map.
    style_key: str | None = None
    # Literal numbering text such as "(ii)". It is *also* kept at the start of
    # `runs`, so it is written into the doc as ordinary text.
    label: str | None = None
    # 1-based page (or slide) number the block starts on.
    page: int = 1
    # Whether the block is ticked by default in the preview.
    selected: bool = True
    # Human-readable explanation of why the parser made this decision. Shown by
    # `notes2gdoc outline --debug` to help tune the heuristics.
    note: str = ""
    # kind="table": the table; kind="image": the picture (e.g. a flowchart)
    table: Table | None = None
    image: Image | None = None
    # A deliberate blank line (from a Word document), kept to separate sections.
    # Ticked/unticked along with the item before it.
    spacer: bool = False
    # For numbered list items (kind="bullet"): the Google Docs numbering
    # preset, e.g. "NUMBERED_DECIMAL_ALPHA_ROMAN". None = an ordinary bullet.
    numbered: str | None = None
    # The first item of a numbered list, when it directly follows another list
    # (so it starts again at 1 instead of carrying on that list's numbers).
    list_start: bool = False
    # A paragraph sitting between the items of the numbered list above it
    # (e.g. a note under item 1, before item 2). It's indented under its item
    # and the list's numbering carries on after it.
    in_list: bool = False

    @property
    def text(self) -> str:
        if self.table is not None:
            return " | ".join(c.text for c in self.table.cells)
        if self.image is not None:
            return self.image.alt
        return "".join(r.text for r in self.runs)


@dataclass
class Document:
    blocks: list[Block]
    source_path: str = ""
    # "document" for ordinary pages, "slides" for slide decks.
    layout: str = "document"
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Run helpers
# --------------------------------------------------------------------------- #

def merge_runs(runs: list[Run]) -> list[Run]:
    """Join neighbouring runs that share a style, and drop empty runs.

    Whitespace-only runs take on the style of the run before them, so a plain
    space between two italic words doesn't split the italic run.
    """
    out: list[Run] = []
    for run in runs:
        if not run.text:
            continue
        if out and (out[-1].same_style(run) or (run.text.isspace() and not run.underline)):
            out[-1] = replace(out[-1], text=out[-1].text + run.text)
        elif out and out[-1].text.isspace() and not out[-1].underline:
            # A leading whitespace-only run adopts the next run's style.
            out[-1] = replace(run, text=out[-1].text + run.text)
        else:
            out.append(replace(run))
    return out


def normalise_whitespace(runs: list[Run]) -> list[Run]:
    """Collapse runs of spaces (from justified text, tabs, etc.) to single spaces
    across run boundaries, and trim the start and end of the block."""
    out: list[Run] = []
    prev_space = True  # True at the start, so leading whitespace is dropped
    for run in runs:
        chars = []
        for ch in run.text.replace("\t", " ").replace(" ", " "):
            if ch.isspace():
                if prev_space:
                    continue
                chars.append(" ")
                prev_space = True
            else:
                chars.append(ch)
                prev_space = False
        if chars:
            out.append(replace(run, text="".join(chars)))
    # Trim trailing whitespace
    while out and out[-1].text.endswith(" "):
        stripped = out[-1].text.rstrip(" ")
        if stripped:
            out[-1] = replace(out[-1], text=stripped)
        else:
            out.pop()
    return merge_runs(out)
