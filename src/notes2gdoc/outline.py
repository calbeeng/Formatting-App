"""Render a Document as indented, Markdown-like text for checking the parser.

    # 1. LIQUIDATION                       <- Heading 1  (one '#' per level)
    ## (a) Overview of Liquidation          <- Heading 2
    - Part 8 of the IRDA                     <- bullet, two spaces per nesting level
    Extra Readings:                          <- normal paragraph

Inline styles (unless plain=True): **bold**, *italic*, __underline__,
^{superscript}, _{subscript}, and {#00B0F0|coloured text}.
Unticked blocks are prefixed with "(skipped) ".
"""

from __future__ import annotations

from .config import Settings
from .model import Block, Document, Run


def render_run(run: Run, strip_colour: bool = False) -> str:
    text = run.text
    core = text.strip(" ")
    if not core:
        return text
    lead = text[: len(text) - len(text.lstrip(" "))]
    trail = text[len(text.rstrip(" ")):]
    if run.superscript:
        core = f"^{{{core}}}"
    if run.subscript:
        core = f"_{{{core}}}"
    if run.underline:
        core = f"__{core}__"
    if run.italic:
        core = f"*{core}*"
    if run.bold:
        core = f"**{core}**"
    if run.color and not strip_colour:
        core = f"{{{run.color}|{core}}}"
    return lead + core + trail


def _render_table(block: Block, settings: Settings, plain: bool) -> str:
    """One line per row: | cell | cell |. Inside a cell, items are separated by
    " / " and bullets start with "• ". A merged cell is followed by "<<" markers
    for the columns it spans."""
    t = block.table
    rows = []
    for r in range(t.n_rows):
        cells = []
        for c in range(t.n_cols):
            cell = t.cell(r, c)
            if cell is None:
                continue
            parts = []
            for b in cell.blocks:
                text = b.text if plain else "".join(render_run(x, settings.strip_colour) for x in b.runs)
                parts.append(("• " if b.kind == "bullet" else "") + text)
            cells.append(" / ".join(parts) + " <<" * (cell.colspan - 1))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def render_block(block: Block, settings: Settings, plain: bool = False) -> str:
    if block.kind == "table":
        line = _render_table(block, settings, plain)
        return line if block.selected else "(skipped) " + line
    if block.kind == "image":
        img = block.image
        line = f"[Picture: {img.width_pt:.0f}x{img.height_pt:.0f}pt]"
        return line if block.selected else "(skipped) " + line
    if plain:
        text = block.text
    else:
        text = "".join(render_run(r, settings.strip_colour) for r in block.runs)
    if block.kind == "heading":
        rank = settings.heading_rank(block.style_key)
        prefix = "#" * rank + " " if rank else ""
        line = prefix + text
    elif block.kind == "bullet":
        line = "  " * block.level + "- " + text
    else:
        line = "  " * block.level + text  # paragraphs inside a list are indented
    if not block.selected:
        line = "(skipped) " + line
    return line


def render_outline(doc: Document, settings: Settings | None = None, plain: bool = False,
                   debug: bool = False, include_skipped: bool = True) -> str:
    settings = settings or Settings()
    out = []
    for b in doc.blocks:
        if not include_skipped and not b.selected:
            continue
        line = render_block(b, settings, plain)
        if debug:
            line += f"    [p{b.page}{'; ' + b.note if b.note else ''}]"
        out.append(line)
    return "\n".join(out) + "\n"
