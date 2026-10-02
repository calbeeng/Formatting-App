"""Reading the target Google Doc: its ID from a URL, where it ends, its
headings, which bullet style it already uses, and its usable page width.

All functions take the JSON returned by `documents().get()`.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_DOC_ID = re.compile(r"/document/d/([a-zA-Z0-9_-]{20,})")
_BARE_ID = re.compile(r"^[a-zA-Z0-9_-]{25,}$")


def doc_id_from_url(text: str) -> str | None:
    """Accepts a full Google Doc link, or just the long ID from it."""
    text = text.strip()
    m = _DOC_ID.search(text)
    if m:
        return m.group(1)
    if _BARE_ID.match(text):
        return text
    return None


def doc_url(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/edit"


@dataclass
class EndState:
    """Facts about the end of the document body."""

    end_index: int           # index of the body's final newline (insert here = end of last paragraph)
    last_para_start: int     # start index of the last paragraph
    last_para_empty: bool    # last paragraph has no text
    only_empty_para: bool    # the whole document is a single empty paragraph


def body_content(doc: dict) -> list[dict]:
    return doc.get("body", {}).get("content", [])


def _para_text(par: dict) -> str:
    out = []
    for el in par.get("elements", []):
        if "textRun" in el:
            out.append(el["textRun"].get("content", ""))
        elif "inlineObjectElement" in el:
            out.append("￼")  # an image counts as content
    return "".join(out)


def end_state(doc: dict) -> EndState:
    content = body_content(doc)
    last = content[-1]
    end_index = last["endIndex"] - 1
    paras = [c for c in content if "paragraph" in c]
    last_para = last if "paragraph" in last else paras[-1]
    text = _para_text(last_para["paragraph"]).rstrip("\n")
    # Body = sectionBreak + paragraphs; "only empty" = exactly one paragraph, empty
    only_empty = len([c for c in content if "sectionBreak" not in c]) == 1 and not text
    return EndState(end_index, last_para["startIndex"], not text, only_empty)


@dataclass
class DocHeading:
    text: str
    level: int          # 1 for HEADING_1 ... 6; 0 for TITLE
    start_index: int


def headings(doc: dict) -> list[DocHeading]:
    """The doc's existing headings, for the "insert after heading" option."""
    out = []
    for c in body_content(doc):
        par = c.get("paragraph")
        if not par:
            continue
        style = par.get("paragraphStyle", {}).get("namedStyleType", "")
        m = re.match(r"HEADING_(\d)", style)
        level = int(m.group(1)) if m else (0 if style == "TITLE" else None)
        if level is None:
            continue
        text = _para_text(par).strip()
        if text:
            out.append(DocHeading(text, level, c["startIndex"]))
    return out


# Map of the first-level glyph Google Docs shows -> the preset that produces it.
GLYPH_PRESETS = {
    "●": "BULLET_DISC_CIRCLE_SQUARE",
    "•": "BULLET_DISC_CIRCLE_SQUARE",
    "❖": "BULLET_DIAMONDX_ARROW3D_SQUARE",
    "☐": "BULLET_CHECKBOX",
    "❏": "BULLET_CHECKBOX",
    "→": "BULLET_ARROW_DIAMOND_DISC",
    "➔": "BULLET_ARROW_DIAMOND_DISC",
    "★": "BULLET_STAR_CIRCLE_SQUARE",
    "➢": "BULLET_ARROW3D_CIRCLE_SQUARE",
    "◄": "BULLET_LEFTTRIANGLE_DIAMOND_DISC",
    "◆": "BULLET_DIAMOND_CIRCLE_SQUARE",
    "◇": "BULLET_DIAMONDX_HOLLOWDIAMOND_SQUARE",
    "○": "BULLET_DISC_CIRCLE_SQUARE",
    "■": "BULLET_DISC_CIRCLE_SQUARE",
    "-": "BULLET_DISC_CIRCLE_SQUARE",
}
DEFAULT_PRESET = "BULLET_DISC_CIRCLE_SQUARE"


def bullet_preset(doc: dict) -> str:
    """The preset closest to the bullets the doc already uses most.

    Numbered lists are ignored (we only write unnumbered bullets). If the doc
    has no bulleted lists yet, use the default disc/circle/square.
    """
    lists = doc.get("lists", {})
    usage: Counter = Counter()
    for c in body_content(doc):
        bullet = c.get("paragraph", {}).get("bullet")
        if bullet and bullet.get("listId") in lists:
            usage[bullet["listId"]] += 1
    for list_id, _ in usage.most_common():
        levels = lists[list_id].get("listProperties", {}).get("nestingLevels", [])
        if not levels:
            continue
        glyph = levels[0].get("glyphSymbol")
        if glyph:  # unordered list
            return GLYPH_PRESETS.get(glyph, DEFAULT_PRESET)
    return DEFAULT_PRESET


def usable_width_pt(doc: dict) -> float:
    """Page width minus left and right margins (default A4/Letter-ish 468pt)."""
    style = doc.get("documentStyle", {})
    try:
        width = style["pageSize"]["width"]["magnitude"]
        left = style.get("marginLeft", {}).get("magnitude", 72)
        right = style.get("marginRight", {}).get("magnitude", 72)
        return max(100.0, width - left - right)
    except (KeyError, TypeError):
        return 468.0


def paragraph_text(element: dict) -> str:
    return _para_text(element.get("paragraph", {}))


def body_end(doc: dict) -> int:
    return body_content(doc)[-1]["endIndex"]


def element_starting_at(doc: dict, index: int) -> dict | None:
    return next((c for c in body_content(doc) if c.get("startIndex") == index), None)


def element_ending_at(doc: dict, index: int) -> dict | None:
    return next((c for c in body_content(doc) if c.get("endIndex") == index), None)


def section_end(doc: dict, heading: DocHeading) -> int | None:
    """Start index of the next heading at the same or a higher level than
    `heading` (so the new notes go at the end of its section), or None if the
    section runs to the end of the document."""
    hs = headings(doc)
    idx = next((i for i, h in enumerate(hs) if h.start_index == heading.start_index and h.text == heading.text),
               None)
    if idx is None:  # the doc changed since the list was loaded: match by text
        idx = next((i for i, h in enumerate(hs) if h.text == heading.text), None)
    if idx is None:
        raise ValueError(f"The heading “{heading.text}” is no longer in the document.")
    for h in hs[idx + 1:]:
        if h.level <= heading.level:
            return h.start_index
    return None


@dataclass
class Slot:
    """What sits just before the place we're writing to."""

    anchor: int | None       # where existing content resumes (None = end of doc)
    doc_is_empty: bool       # the whole doc is one empty paragraph
    prev_is_table: bool      # the element just before is a table
    prev_start: int          # start index of the paragraph just before
    prev_newline: int        # index of that paragraph's final newline
    prev_empty: bool         # that paragraph has no text


def slot_before(doc: dict, anchor: int | None) -> Slot:
    content = [c for c in body_content(doc) if "sectionBreak" not in c]
    if anchor is None:
        prev = content[-1]
    else:
        prev = next((c for c in reversed(content) if c["endIndex"] <= anchor), content[0])
    is_empty_doc = len(content) == 1 and "paragraph" in content[0] and not paragraph_text(content[0]).strip("\n")
    if "paragraph" not in prev:
        return Slot(anchor, False, True, prev["startIndex"], prev["endIndex"] - 1, False)
    return Slot(anchor, is_empty_doc, False, prev["startIndex"], prev["endIndex"] - 1,
                not paragraph_text(prev).strip("\n"))


def find_table_at(doc: dict, index: int) -> dict | None:
    """The first table that starts at or after `index`."""
    for c in body_content(doc):
        if "table" in c and c["startIndex"] >= index:
            return c
    return None
