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


_TAB_ID = re.compile(r"[?&#]tab=([a-zA-Z0-9._-]+)")


def tab_id_from_url(text: str) -> str | None:
    """The tab a Google Doc link points at ("…/edit?tab=t.abc123"), if any."""
    m = _TAB_ID.search(text or "")
    return m.group(1) if m else None


def doc_url(doc_id: str, tab_id: str | None = None) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/edit" + (f"?tab={tab_id}" if tab_id else "")


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


@dataclass
class DocTab:
    """One tab of the doc (tabs can sit inside other tabs: `depth`)."""

    tab_id: str
    title: str
    depth: int
    headings: list[DocHeading]


def _walk_tabs(tabs: list[dict], depth: int = 0):
    for t in tabs:
        yield t, depth
        yield from _walk_tabs(t.get("childTabs", []), depth + 1)


def doc_tabs(doc: dict) -> list[DocTab]:
    """Every tab, in the order Google Docs lists them. `doc` must have been
    fetched with includeTabsContent=True."""
    out = []
    for t, depth in _walk_tabs(doc.get("tabs", [])):
        props = t.get("tabProperties", {})
        if "documentTab" in t and props.get("tabId"):
            out.append(DocTab(props["tabId"], props.get("title") or "Untitled tab", depth,
                              headings(t["documentTab"])))
    return out


def tab_view(doc: dict, tab_id: str) -> dict | None:
    """That tab's content, shaped like a whole one-tab document (body, lists,
    documentStyle), so everything else here can read it the same way."""
    for t, _ in _walk_tabs(doc.get("tabs", [])):
        if t.get("tabProperties", {}).get("tabId") == tab_id and "documentTab" in t:
            return {"documentId": doc.get("documentId"), "title": doc.get("title"), **t["documentTab"]}
    return None


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
    prev_plain: bool = False  # ...and has no bullet or indent (a table can follow it directly)


def slot_before(doc: dict, anchor: int | None) -> Slot:
    content = [c for c in body_content(doc) if "sectionBreak" not in c]
    if anchor is None:
        prev = content[-1]
    else:
        prev = next((c for c in reversed(content) if c["endIndex"] <= anchor), content[0])
    is_empty_doc = len(content) == 1 and "paragraph" in content[0] and not paragraph_text(content[0]).strip("\n")
    if "paragraph" not in prev:
        return Slot(anchor, False, True, prev["startIndex"], prev["endIndex"] - 1, False)
    style = prev["paragraph"].get("paragraphStyle", {})
    plain = ("bullet" not in prev["paragraph"]
             and (style.get("indentStart") or {}).get("magnitude", 0) == 0
             and (style.get("indentFirstLine") or {}).get("magnitude", 0) == 0)
    return Slot(anchor, is_empty_doc, False, prev["startIndex"], prev["endIndex"] - 1,
                not paragraph_text(prev).strip("\n"), plain)


def find_table_at(doc: dict, index: int) -> dict | None:
    """The table we just inserted at `index`: Docs puts a newline first, so
    it starts at index + 1 (allowing a little slack)."""
    for c in body_content(doc):
        if "table" in c and index <= c["startIndex"] <= index + 2:
            return c
    return None
