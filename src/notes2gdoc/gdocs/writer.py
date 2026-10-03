"""Build Google Docs API requests from Blocks. Pure functions, no network.

How a run of paragraphs/bullets/headings is written
---------------------------------------------------
We always fill an *empty paragraph we own*: the client first makes sure the
document ends with an empty paragraph (see client.py), then calls
`text_requests(blocks, index)` where `index` is that paragraph's start.

1. insertText: all paragraphs joined by "\\n", in one go. Bullet paragraphs
   start with one "\\t" per nesting level; that's how the Docs API learns the
   nesting (step 6 turns the tabs into real indent levels and removes them).
   The last paragraph is ended by the empty paragraph's own newline.
2. deleteParagraphBullets over the whole range, in case it inherited a bullet.
   This must come BEFORE step 3: when Google removes a bullet it keeps the
   paragraph where it was by adding a left indent, which step 3 then clears.
   (Doing it the other way round left headings after a list indented.)
3. Clear inherited paragraph formatting. Inserted text copies the formatting of
   the paragraph it's inserted into, so we set namedStyleType and *reset*
   alignment/indents/spacing. Resetting means listing a field in the mask
   without a value, which makes the doc's named style (Heading 1, Normal
   text...) supply them.
4. Clear inherited text formatting (font, size, colour, bold...), again by
   resetting, so the named style decides fonts and sizes. We never set a font
   family or size ourselves.
5. Apply the source's inline styles run by run: bold, italic, underline,
   superscript/subscript, colour.
6. createParagraphBullets for each group of consecutive bullets, last group
   first. Removing the tabs shifts later text left, so going backwards keeps
   every earlier range valid.

The safety rule "never modify existing content" holds because every range
here starts at `index` (the empty paragraph we own) or later, and the only
text-changing request is insertText.
"""

from __future__ import annotations

import re

from ..config import Settings
from ..model import Block, Run

# Paragraph properties that the named style should control (reset on our text).
PARA_RESET_FIELDS = [
    "alignment", "indentStart", "indentEnd", "indentFirstLine",
    "spaceAbove", "spaceBelow", "lineSpacing", "direction",
]
# Google Docs indents each bullet level by 36pt (half an inch).
LIST_INDENT_PT = 36

# Text properties reset before applying the source's own styles.
TEXT_RESET_FIELDS = [
    "bold", "italic", "underline", "strikethrough", "smallCaps", "baselineOffset",
    "foregroundColor", "backgroundColor", "weightedFontFamily", "fontSize", "link",
]

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-]")


def _clean(text: str) -> str:
    """Text safe to insert: no newlines/tabs inside a paragraph, no control
    characters, no private-use glyphs (leftover symbol-font characters)."""
    return _CONTROL.sub("", text.replace("\n", " ").replace("\t", " "))


def _range(start: int, end: int, tab_id: str | None) -> dict:
    r = {"startIndex": start, "endIndex": end}
    if tab_id:
        r["tabId"] = tab_id
    return r


def _location(index: int, tab_id: str | None) -> dict:
    loc = {"index": index}
    if tab_id:
        loc["tabId"] = tab_id
    return loc


def hex_to_rgb(hex_colour: str) -> dict:
    h = hex_colour.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def run_style(run: Run, strip_colour: bool) -> tuple[dict, list[str]]:
    """(textStyle, fields) for a run's non-default styling; empty if plain."""
    style: dict = {}
    if run.bold:
        style["bold"] = True
    if run.italic:
        style["italic"] = True
    if run.underline:
        style["underline"] = True
    if run.superscript:
        style["baselineOffset"] = "SUPERSCRIPT"
    elif run.subscript:
        style["baselineOffset"] = "SUBSCRIPT"
    if run.color and not strip_colour:
        style["foregroundColor"] = {"color": {"rgbColor": hex_to_rgb(run.color)}}
    if run.highlight and not strip_colour:
        style["backgroundColor"] = {"color": {"rgbColor": hex_to_rgb(run.highlight)}}
    return style, list(style.keys())


def paragraph_style_for(block: Block, settings: Settings) -> str:
    if block.kind == "heading":
        return settings.named_style(block.style_key)
    return "NORMAL_TEXT"


def text_requests(
    blocks: list[Block],
    index: int,
    settings: Settings,
    bullet_preset: str,
    tab_id: str | None = None,
    flush_left: bool = False,
) -> tuple[list[dict], int]:
    """Requests that fill the empty paragraph starting at `index` with `blocks`
    (headings, bullets and paragraphs only).

    Returns (requests, net_length): net_length is how many characters the
    document grows by once the bullet tabs have been removed.
    """
    paras: list[tuple[Block, str, list[tuple[int, Run]]]] = []  # block, full text, (offset, run)
    for b in blocks:
        prefix = "\t" * b.level if b.kind == "bullet" else ""
        text = prefix
        runs: list[tuple[int, Run]] = []
        for r in b.runs:
            t = _clean(r.text)
            if t:
                runs.append((len(text), Run(t, r.bold, r.italic, r.underline, r.superscript, r.subscript,
                                            r.color, r.highlight)))
                text += t
        if b.hanging:
            # The gap after the typed number becomes a tab, so the text lines
            # up with the hanging indent like a real list's text does
            from ..lists import marker_of
            m = marker_of(text)
            cut = len(m[1]) if m else -1
            if 0 <= cut < len(text) and text[cut] == " ":
                text = text[:cut] + "\t" + text[cut + 1:]
        paras.append((b, text, runs))
    if not paras:
        return [], 0

    body = "\n".join(p[1] for p in paras)
    reqs: list[dict] = [{"insertText": {"location": _location(index, tab_id), "text": body}}]

    # Start index of each paragraph
    starts = []
    pos = index
    for _, text, _ in paras:
        starts.append(pos)
        pos += len(text) + 1  # + its newline
    end_all = pos  # just past the last paragraph's newline (the pre-existing one)

    # 2. No inherited bullets (before resetting indents; see module notes)
    reqs.append({"deleteParagraphBullets": {"range": _range(index, end_all, tab_id)}})

    # 3. Paragraph styles, merging neighbours with the same style
    i = 0
    while i < len(paras):
        style = paragraph_style_for(paras[i][0], settings)
        j = i
        while j + 1 < len(paras) and paragraph_style_for(paras[j + 1][0], settings) == style:
            j += 1
        para_style = {"namedStyleType": style}
        if settings.justify_text:
            para_style["alignment"] = "JUSTIFIED"
        reqs.append({"updateParagraphStyle": {
            "range": _range(starts[i], starts[j] + len(paras[j][1]) + 1, tab_id),
            "paragraphStyle": para_style,
            "fields": ",".join(["namedStyleType", *PARA_RESET_FIELDS]),
        }})
        i = j + 1

    # Flush left: headings and paragraphs start at the left margin even if the
    # doc's Normal text style has an indent (Settings.flush_left; always in
    # table cells, where a hanging indent in a narrow cell looks broken).
    # Bullets get their indent from the list, and quotes inside lists get
    # theirs just below.
    if flush_left or settings.flush_left:
        for (b, text, _), start in zip(paras, starts):
            if b.kind != "bullet" and not (b.kind == "paragraph" and (b.level > 0 or b.hanging)):
                reqs.append(zero_indent_request(start, start + len(text) + 1, tab_id))

    # Paragraphs inside a list (e.g. a quote under a bullet) are indented to
    # line up with the bullet text: one list level = LIST_INDENT_PT, the same
    # step Google Docs uses for its bullet levels.
    # Typed list items kept as text hang like a list item: number at the
    # list's number position, text (and wrapped lines) at its text position
    for (b, text, _), start in zip(paras, starts):
        if b.kind == "paragraph" and b.hanging:
            reqs.append({"updateParagraphStyle": {
                "range": _range(start, start + len(text) + 1, tab_id),
                "paragraphStyle": {
                    "indentStart": {"magnitude": LIST_INDENT_PT * (b.level + 1), "unit": "PT"},
                    "indentFirstLine": {"magnitude": LIST_INDENT_PT * b.level + LIST_INDENT_PT / 2, "unit": "PT"},
                },
                "fields": "indentStart,indentFirstLine",
            }})
    for (b, text, _), start in zip(paras, starts):
        if b.kind == "paragraph" and b.level > 0 and not b.hanging:
            indent = {"magnitude": LIST_INDENT_PT * b.level, "unit": "PT"}
            reqs.append({"updateParagraphStyle": {
                "range": _range(start, start + len(text) + 1, tab_id),
                "paragraphStyle": {"indentStart": indent, "indentFirstLine": indent},
                "fields": "indentStart,indentFirstLine",
            }})

    # 4. Reset inherited text formatting (only where there is text)
    if body:
        reqs.append({"updateTextStyle": {
            "range": _range(index, index + len(body), tab_id),
            "textStyle": {},
            "fields": ",".join(TEXT_RESET_FIELDS),
        }})

    # 5. The source's inline styles
    for (b, _, runs), start in zip(paras, starts):
        for offset, run in runs:
            style, fields = run_style(run, settings.strip_colour)
            core = run.text.strip(" ")
            if fields and core:
                # Leave edge spaces unstyled (an underlined space looks like a stray line)
                s = start + offset + (len(run.text) - len(run.text.lstrip(" ")))
                reqs.append({"updateTextStyle": {
                    "range": _range(s, s + len(core), tab_id),
                    "textStyle": style,
                    "fields": ",".join(fields),
                }})

    # 6. Bullets: groups of consecutive list paragraphs of the same kind
    # (ordinary bullets, or numbered with a given style), last group first
    def list_kind(b):
        return b.numbered if b.kind == "bullet" else None

    # A numbered list also spans paragraphs sitting inside it (Block.in_list):
    # the whole range becomes one list, then those paragraphs lose their
    # number again, so the list's numbering carries on after them.
    def tab_count(i):
        b, text, _ = paras[i]
        return len(text) - len(text.lstrip("\t")) if b.kind == "bullet" else 0

    groups: list[tuple[int, int]] = []
    k = 0
    while k < len(paras):
        if paras[k][0].kind == "bullet":
            g = k
            while True:
                j = g + 1
                if paras[k][0].numbered:
                    while j < len(paras) and paras[j][0].in_list:
                        j += 1
                if (j < len(paras) and paras[j][0].kind == "bullet"
                        and list_kind(paras[j][0]) == list_kind(paras[k][0])
                        and not paras[j][0].list_start):
                    g = j
                else:
                    break
            groups.append((k, g))
            k = g + 1
        else:
            k += 1
    for first, last in reversed(groups):
        reqs.append({"createParagraphBullets": {
            "range": _range(starts[first], starts[last] + len(paras[last][1]) + 1, tab_id),
            "bulletPreset": paras[first][0].numbered or bullet_preset,
        }})
        removed = 0  # tabs already consumed by this group's items above
        for i in range(first, last + 1):
            b, text, _ = paras[i]
            if b.kind != "bullet":
                s0 = starts[i] - removed
                rng = _range(s0, s0 + len(text) + 1, tab_id)
                indent = {"magnitude": LIST_INDENT_PT * b.level, "unit": "PT"}
                reqs.append({"deleteParagraphBullets": {"range": rng}})
                reqs.append({"updateParagraphStyle": {
                    "range": rng,
                    "paragraphStyle": {"indentStart": indent, "indentFirstLine": indent},
                    "fields": "indentStart,indentFirstLine",
                }})
            removed += tab_count(i)

    tabs = sum(len(text) - len(text.lstrip("\t")) for b, text, _ in paras if b.kind == "bullet")
    return reqs, len(body) - tabs


def zero_indent_request(start: int, end: int, tab_id: str | None = None) -> dict:
    """Explicitly no indent, overriding whatever the doc's named style says."""
    zero = {"magnitude": 0, "unit": "PT"}
    return {"updateParagraphStyle": {
        "range": _range(start, end, tab_id),
        "paragraphStyle": {"indentStart": zero, "indentFirstLine": zero},
        "fields": "indentStart,indentFirstLine",
    }}


def plain_paragraph_requests(start: int, end: int, tab_id: str | None = None) -> list[dict]:
    """Make paragraphs we created (e.g. the blank separator) plain Normal text."""
    # Bullets off first: removing a bullet adds an indent that the reset then clears
    return [
        {"deleteParagraphBullets": {"range": _range(start, end, tab_id)}},
        {"updateParagraphStyle": {
            "range": _range(start, end, tab_id),
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "fields": ",".join(["namedStyleType", *PARA_RESET_FIELDS]),
        }},
    ]


def image_requests(index: int, uri: str, width_pt: float, height_pt: float,
                   max_width_pt: float, tab_id: str | None = None) -> list[dict]:
    """Put a picture, centred, into the empty paragraph at `index`, scaled
    down to fit the page width (never scaled up)."""
    scale = min(1.0, max_width_pt / width_pt) if width_pt else 1.0
    centre = {"updateParagraphStyle": {
        "range": _range(index, index + 1, tab_id),
        "paragraphStyle": {"alignment": "CENTER"},
        "fields": "alignment",
    }}
    return plain_paragraph_requests(index, index + 1, tab_id) + [centre, {"insertInlineImage": {
        "location": _location(index, tab_id),
        "uri": uri,
        "objectSize": {
            "width": {"magnitude": round(width_pt * scale, 1), "unit": "PT"},
            "height": {"magnitude": round(height_pt * scale, 1), "unit": "PT"},
        },
    }}]


# --------------------------------------------------------------------------- #
# Splitting a Document into write segments
# --------------------------------------------------------------------------- #

MAX_BLOCKS_PER_TEXT_SEGMENT = 150


def segments(blocks: list[Block]) -> list[tuple[str, list[Block]]]:
    """Split blocks into ("text", [...]), ("table", [block]), ("image", [block])
    segments, in order. Long text runs are split so each API call stays small."""
    out: list[tuple[str, list[Block]]] = []
    for b in blocks:
        if b.kind in ("table", "image"):
            out.append((b.kind, [b]))
        elif not b.text.strip() and not b.spacer:
            continue
        elif out and out[-1][0] == "text" and len(out[-1][1]) < MAX_BLOCKS_PER_TEXT_SEGMENT:
            out[-1][1].append(b)
        else:
            out.append(("text", [b]))
    return out


def cell_background_requests(table_start: int, cells, strip_colour: bool) -> list[dict]:
    """Cell shading (e.g. coloured header cells), unless colour is stripped."""
    if strip_colour:
        return []
    reqs = []
    for c in cells:
        if not c.background:
            continue
        reqs.append({"updateTableCellStyle": {
            "tableRange": {
                "tableCellLocation": {"tableStartLocation": {"index": table_start},
                                      "rowIndex": c.row, "columnIndex": c.col},
                "rowSpan": 1, "columnSpan": 1,
            },
            "tableCellStyle": {"backgroundColor": {"color": {"rgbColor": hex_to_rgb(c.background)}}},
            "fields": "backgroundColor",
        }})
    return reqs


def table_width_requests(table_start: int, col_widths: list[float], n_cols: int,
                         page_width_pt: float) -> list[dict]:
    """Make a table span the full page width, keeping the source's column
    proportions (equal columns if unknown). Google's default is narrower."""
    weights = col_widths if len(col_widths) == n_cols and all(w > 0 for w in col_widths) else [1.0] * n_cols
    total = sum(weights)
    reqs = []
    for i, w in enumerate(weights):
        reqs.append({"updateTableColumnProperties": {
            "tableStartLocation": {"index": table_start},
            "columnIndices": [i],
            "tableColumnProperties": {
                "widthType": "FIXED_WIDTH",
                # Google's minimum column width is 5pt
                "width": {"magnitude": max(5.0, round(page_width_pt * w / total, 1)), "unit": "PT"},
            },
            "fields": "widthType,width",
        }})
    return reqs
