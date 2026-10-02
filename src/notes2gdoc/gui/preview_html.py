"""Render a Document as HTML for the preview pane, so it looks roughly like it
will in Google Docs: real heading sizes, nested bullets, inline styles, colour."""

from __future__ import annotations

from html import escape

from ..config import Settings
from ..model import Block, Document, Run

# Same glyph cycle as Google Docs' default BULLET_DISC_CIRCLE_SQUARE preset
BULLET_GLYPHS = ["●", "○", "■"]
HEADING_SIZES = {1: "20pt", 2: "16pt", 3: "13pt", 4: "12pt"}

CSS = """
body { font-size: 11pt; }
p { margin: 0; }
.note { color: #888888; font-size: 8pt; }
.skipped { color: #AAAAAA; }
.tag { color: #B07000; font-size: 8pt; }
"""


def run_html(run: Run, strip_colour: bool) -> str:
    text = escape(run.text)
    styles = []
    if run.bold:
        styles.append("font-weight:bold")
    if run.italic:
        styles.append("font-style:italic")
    if run.underline:
        styles.append("text-decoration:underline")
    if run.superscript:
        styles.append("vertical-align:super")
    if run.subscript:
        styles.append("vertical-align:sub")
    if run.color and not strip_colour:
        styles.append(f"color:{run.color}")
    if run.highlight and not strip_colour:
        styles.append(f"background-color:{run.highlight}")
    return f'<span style="{";".join(styles)}">{text}</span>' if styles else text


def _cell_html(blocks: list[Block], settings: Settings) -> str:
    parts = []
    for b in blocks:
        if b.spacer:
            parts.append("<p>&nbsp;</p>")
            continue
        inner = "".join(run_html(r, settings.strip_colour) for r in b.runs)
        if b.kind == "bullet":
            glyph = BULLET_GLYPHS[b.level % len(BULLET_GLYPHS)]
            parts.append(f'<p style="margin-left:{12 * b.level}px">{glyph}&nbsp;{inner}</p>')
        else:
            parts.append(f"<p>{inner}</p>")
    return "".join(parts)


def table_html(block: Block, settings: Settings) -> str:
    t = block.table
    rows = []
    for r in range(t.n_rows):
        cells = []
        for c in range(t.n_cols):
            cell = t.cell(r, c)
            if cell is None:
                continue
            span = f' colspan="{cell.colspan}"' if cell.colspan > 1 else ""
            span += f' rowspan="{cell.rowspan}"' if cell.rowspan > 1 else ""
            bg = f' bgcolor="{cell.background}"' if cell.background and not settings.strip_colour else ""
            cells.append(f'<td valign="top"{span}{bg}>{_cell_html(cell.blocks, settings)}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return ('<table border="1" cellspacing="0" cellpadding="5" width="100%" '
            'style="border-color:#999999; margin-top:8px; margin-bottom:8px">' + "".join(rows) + "</table>")


def block_html(block: Block, settings: Settings, show_notes: bool, image_name: str = "") -> str:
    if block.spacer:
        return "<p>&nbsp;</p>"
    inner = "".join(run_html(r, settings.strip_colour) for r in block.runs)
    align = "text-align: justify;" if settings.justify_text else ""
    note = ""
    if show_notes:
        note = f' <span class="note">[p{block.page}{"; " + escape(block.note) if block.note else ""}]</span>'
    tag = "" if block.selected else '<span class="tag">SKIPPED&nbsp;&nbsp;</span>'
    wrap_open, wrap_close = ("<span class='skipped'>", "</span>") if not block.selected else ("", "")

    if block.kind == "table":
        return f"<p>{tag}{note}</p>{table_html(block, settings)}" if (tag or note) else table_html(block, settings)
    if block.kind == "image":
        # Shown at most 600px wide, keeping its proportions
        w = min(600, block.image.width_pt)
        return f'<p style="margin-top:8px" align="center">{tag}<img src="{image_name}" width="{w:.0f}">{note}</p>'
    if block.kind == "heading":
        rank = settings.heading_rank(block.style_key)
        if rank:
            size = HEADING_SIZES.get(rank, "12pt")
            return (f'<p style="font-size:{size}; margin-top:12px; margin-bottom:4px">'
                    f"{tag}{wrap_open}{inner}{wrap_close}{note}</p>")
    if block.kind == "bullet":
        glyph = BULLET_GLYPHS[block.level % len(BULLET_GLYPHS)]
        indent = 18 + 24 * block.level
        return (f'<table style="margin-left:{indent}px; margin-top:2px" cellspacing="0" cellpadding="0">'
                f'<tr><td width="18" valign="top">{glyph}</td>'
                f'<td style="{align}">{tag}{wrap_open}{inner}{wrap_close}{note}</td></tr></table>')
    indent = 18 + 24 * block.level if block.level else 0  # paragraph inside a list
    return f'<p style="margin-top:6px; margin-left:{indent}px; {align}">{tag}{wrap_open}{inner}{wrap_close}{note}</p>'


def document_html(doc: Document, settings: Settings, show_notes: bool = False,
                  show_skipped: bool = True) -> tuple[str, dict[str, bytes]]:
    """Returns (html, images) where images maps the <img src> names used in the
    HTML to PNG bytes; the caller registers them with the text browser."""
    parts = [f"<html><head><style>{CSS}</style></head><body>"]
    images: dict[str, bytes] = {}
    for i, b in enumerate(doc.blocks):
        if not show_skipped and not b.selected:
            continue
        name = ""
        if b.kind == "image":
            name = f"notes2gdoc-image-{i}.png"
            images[name] = b.image.png
        parts.append(block_html(b, settings, show_notes, name))
    parts.append("</body></html>")
    return "".join(parts), images
