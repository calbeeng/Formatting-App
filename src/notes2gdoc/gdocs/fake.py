"""A small in-memory imitation of the Google Docs API, used for:

* automated tests: replay the requests we'd send and check the result has the
  right text, styles, bullets and tables, with every index in range;
* dry runs: produce exactly the requests the app would send, without touching
  the real document.

It models the document the way the real API does: a flat run of characters
where each paragraph ends with "\\n", the paragraph's style lives on that newline,
and a table occupies marker positions (table/row/cell starts) around its
cells' own paragraphs. Index 0 is the section break, so body text starts at 1.
It's deliberately strict: invalid ranges raise errors, like the real API.
"""

from __future__ import annotations

import copy
import itertools

TABLE_START, ROW_START, CELL_START, TABLE_END, IMAGE = "", "", "", "", ""
MARKERS = {TABLE_START, ROW_START, CELL_START, TABLE_END}

PRESET_GLYPHS = {
    "BULLET_DISC_CIRCLE_SQUARE": "●",
    "BULLET_DIAMONDX_ARROW3D_SQUARE": "❖",
    "BULLET_CHECKBOX": "☐",
    "BULLET_ARROW_DIAMOND_DISC": "➔",
    "BULLET_STAR_CIRCLE_SQUARE": "★",
    "BULLET_ARROW3D_CIRCLE_SQUARE": "➢",
    "BULLET_LEFTTRIANGLE_DIAMOND_DISC": "◄",
    "BULLET_DIAMONDX_HOLLOWDIAMOND_SQUARE": "◇",
    "BULLET_DIAMOND_CIRCLE_SQUARE": "◆",
}


class FakeDocsError(Exception):
    """Mirrors the real API rejecting a request (HTTP 400)."""


class _Char:
    __slots__ = ("c", "ts", "ps", "bullet", "image")

    def __init__(self, c, ts=None, ps=None, bullet=None, image=None):
        self.c = c
        self.ts = dict(ts or {})
        self.ps = dict(ps or {}) if c == "\n" else {}
        self.bullet = dict(bullet) if bullet else None
        self.image = image


class FakeDoc:
    def __init__(self, paragraphs: list[tuple[str, str]] | None = None, document_id: str = "fake-doc"):
        """paragraphs: [(text, namedStyleType)]; default = one empty paragraph."""
        self.document_id = document_id
        self.title = "Scratch doc"
        self.seq: list[_Char] = []
        for text, style in paragraphs or [("", "NORMAL_TEXT")]:
            self.seq.extend(_Char(ch) for ch in text)
            self.seq.append(_Char("\n", ps={"namedStyleType": style}))
        self.lists: dict[str, dict] = {}
        self.merges: list[dict] = []
        self.inline_objects: dict[str, dict] = {}
        self._ids = itertools.count(1)
        self.batches: list[list[dict]] = []
        # Set to imitate writing to one tab of a doc with tabs: the content is
        # then that tab's, and every request must name it
        self.tab_id: str | None = None

    @classmethod
    def with_end_state(cls, end_index: int, last_para_empty: bool) -> "FakeDoc":
        """A stand-in for a real document with the same end index and the same
        "is the last paragraph empty?" answer, for realistic dry-run indexes."""
        if end_index <= 1:
            return cls()
        if last_para_empty:
            return cls([("x" * (end_index - 2), "NORMAL_TEXT"), ("", "NORMAL_TEXT")])
        return cls([("x" * (end_index - 1), "NORMAL_TEXT")])

    @classmethod
    def from_document(cls, doc: dict) -> "FakeDoc":
        """An approximate copy of a real document (for dry runs): same
        paragraphs, styles and indexes. Tables and pictures become filler text
        of the same length, so later indexes still line up."""
        paras = []
        for el in doc.get("body", {}).get("content", []):
            if "paragraph" in el:
                p = el["paragraph"]
                text = "".join(e.get("textRun", {}).get("content", "￼") for e in p.get("elements", []))
                paras.append((text.rstrip("\n"), p.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")))
            elif "startIndex" in el:  # table, table of contents, ...
                paras.append(("·" * (el["endIndex"] - el["startIndex"] - 1), "NORMAL_TEXT"))
        fake = cls(paras or None, doc.get("documentId", "fake-doc"))
        fake.title = doc.get("title", fake.title)
        return fake

    # ------------------------------------------------------------------ #
    # Index helpers (index = position + 1)
    # ------------------------------------------------------------------ #
    def _pos(self, index: int) -> int:
        return index - 1

    def _check_range(self, r: dict) -> tuple[int, int]:
        s, e = r["startIndex"], r["endIndex"]
        if not (1 <= s < e <= len(self.seq) + 1):
            raise FakeDocsError(f"Invalid range {s}-{e} (document body is 1-{len(self.seq) + 1})")
        return self._pos(s), self._pos(e)

    def _paragraph_bounds(self, pos: int) -> tuple[int, int]:
        """(first pos, newline pos) of the paragraph containing pos."""
        start = pos
        while start > 0 and self.seq[start - 1].c != "\n" and self.seq[start - 1].c not in MARKERS:
            start -= 1
        end = pos
        while self.seq[end].c != "\n":
            if self.seq[end].c in MARKERS:
                raise FakeDocsError(f"Index {pos + 1} is not inside a paragraph")
            end += 1
        return start, end

    def _paragraphs_in(self, ps: int, pe: int) -> list[tuple[int, int]]:
        """Paragraphs overlapping positions [ps, pe)."""
        out = []
        pos = ps
        while pos < pe and pos < len(self.seq):
            if self.seq[pos].c in MARKERS:
                pos += 1
                continue
            start, end = self._paragraph_bounds(pos)
            out.append((start, end))
            pos = end + 1
        return out

    # ------------------------------------------------------------------ #
    # Requests
    # ------------------------------------------------------------------ #
    def apply(self, requests: list[dict]) -> None:
        self.batches.append(copy.deepcopy(requests))
        for req in requests:
            (kind, body), = req.items()
            handler = getattr(self, "_" + kind, None)
            if handler is None:
                raise FakeDocsError(f"Unsupported request {kind}")
            if kind in ("deleteContentRange", "replaceAllText"):
                raise FakeDocsError("Destructive request")
            self._check_tab(body)
            handler(body)

    def _check_tab(self, value) -> None:
        """Every location and range must point at the tab being written to
        (without a tab ID the real API writes to the doc's first tab)."""
        if isinstance(value, list):
            for v in value:
                self._check_tab(v)
        elif isinstance(value, dict):
            is_position = "index" in value or ("startIndex" in value and "endIndex" in value)
            if is_position and value.get("tabId") != self.tab_id:
                raise FakeDocsError(f"Request for tab {value.get('tabId')!r}, expected {self.tab_id!r}")
            for v in value.values():
                self._check_tab(v)

    def _insertText(self, b):
        index = b["location"]["index"]
        text = b["text"]
        pos = self._pos(index)
        if not (0 <= pos < len(self.seq)) or self.seq[pos].c in MARKERS:
            raise FakeDocsError(f"insertText index {index} is not inside a paragraph")
        _, nl = self._paragraph_bounds(pos)
        ts = self.seq[pos - 1].ts if pos > 0 and self.seq[pos - 1].c not in MARKERS | {"\n"} else self.seq[pos].ts
        para = self.seq[nl]
        new = [_Char(ch, ts, para.ps, para.bullet) for ch in text]
        self.seq[pos:pos] = new

    def _updateParagraphStyle(self, b):
        ps, pe = self._check_range(b["range"])
        fields = b["fields"].split(",")
        for _, nl in self._paragraphs_in(ps, pe):
            for f in fields:
                if f in b["paragraphStyle"]:
                    self.seq[nl].ps[f] = b["paragraphStyle"][f]
                else:
                    self.seq[nl].ps.pop(f, None)

    def _deleteParagraphBullets(self, b):
        # Like the real API: "The nesting level of each paragraph will be
        # visually preserved by adding indent to the start of the paragraph."
        ps, pe = self._check_range(b["range"])
        for _, nl in self._paragraphs_in(ps, pe):
            bullet = self.seq[nl].bullet
            if bullet:
                self.seq[nl].ps["indentStart"] = {"magnitude": 36 * (bullet["nestingLevel"] + 1), "unit": "PT"}
            self.seq[nl].bullet = None

    def _createParagraphBullets(self, b):
        ps, pe = self._check_range(b["range"])
        list_id = f"list{next(self._ids)}"
        self.lists[list_id] = {"listProperties": {"nestingLevels": [
            {"glyphSymbol": PRESET_GLYPHS.get(b["bulletPreset"], "1." if b["bulletPreset"].startswith("NUMBERED") else "●")}]}}
        for start, nl in reversed(self._paragraphs_in(ps, pe)):
            old = self.seq[nl].bullet
            if old:
                # Like the real Docs: a paragraph already in a list stays in
                # it, at its level and with its tabs; the *whole* list takes
                # on the new style instead
                self.lists[old["listId"]] = copy.deepcopy(self.lists[list_id])
                continue
            tabs = 0
            while self.seq[start + tabs].c == "\t":
                tabs += 1
            self.seq[nl + 0].bullet = {"listId": list_id, "nestingLevel": tabs}
            del self.seq[start:start + tabs]

    def _updateTextStyle(self, b):
        ps, pe = self._check_range(b["range"])
        fields = b["fields"].split(",")
        for ch in self.seq[ps:pe]:
            for f in fields:
                if f in b["textStyle"]:
                    ch.ts[f] = b["textStyle"][f]
                else:
                    ch.ts.pop(f, None)

    def _insertTable(self, b):
        index = b["location"]["index"]
        pos = self._pos(index)
        if not (0 <= pos < len(self.seq)) or self.seq[pos].c in MARKERS:
            raise FakeDocsError(f"insertTable index {index} is not inside a paragraph")
        # Like the real editor, a table inserted into a bulleted or indented
        # paragraph is indented to match. Remember that so tests can catch it.
        _, nl = self._paragraph_bounds(pos)
        para = self.seq[nl]
        indent = para.ps.get("indentStart") or {}
        inherited = para.bullet or (indent if indent.get("magnitude", 0) > 0 else None)
        self._insertText({"location": {"index": index}, "text": "\n"})
        cells = [_Char(TABLE_START)]
        cells[0].image = {"inheritedIndent": inherited} if inherited else None
        for _ in range(b["rows"]):
            cells.append(_Char(ROW_START))
            for _ in range(b["columns"]):
                cells.append(_Char(CELL_START))
                cells.append(_Char("\n", ps={"namedStyleType": "NORMAL_TEXT"}))
        cells.append(_Char(TABLE_END))
        self.seq[pos + 1:pos + 1] = cells

    def _updateTableColumnProperties(self, b):
        pos = self._pos(b["tableStartLocation"]["index"])
        if not (0 <= pos < len(self.seq)) or self.seq[pos].c != TABLE_START:
            raise FakeDocsError("tableStartLocation is not the start of a table")
        marker = self.seq[pos]
        info = dict(marker.image or {})
        widths = dict(info.get("columnWidths", {}))
        for i in b["columnIndices"]:
            widths[i] = b["tableColumnProperties"]["width"]["magnitude"]
        info["columnWidths"] = widths
        marker.image = info

    def _updateTableCellStyle(self, b):
        loc = b["tableRange"]["tableCellLocation"]
        pos = self._pos(loc["tableStartLocation"]["index"])
        if not (0 <= pos < len(self.seq)) or self.seq[pos].c != TABLE_START:
            raise FakeDocsError("tableStartLocation is not the start of a table")
        marker = self.seq[pos]
        info = dict(marker.image or {})
        shading = dict(info.get("cellBackgrounds", {}))
        rgb = b["tableCellStyle"]["backgroundColor"]["color"]["rgbColor"]
        shading[(loc["rowIndex"], loc["columnIndex"])] = rgb
        info["cellBackgrounds"] = shading
        marker.image = info

    def _mergeTableCells(self, b):
        self.merges.append(b["tableRange"])

    def _insertInlineImage(self, b):
        index = b["location"]["index"]
        pos = self._pos(index)
        if not (0 <= pos < len(self.seq)) or self.seq[pos].c in MARKERS:
            raise FakeDocsError(f"insertInlineImage index {index} is not inside a paragraph")
        if not b["uri"].startswith("https://"):
            raise FakeDocsError("Image uri must be public https")
        obj_id = f"kix.obj{next(self._ids)}"
        self.inline_objects[obj_id] = {"uri": b["uri"], "size": b.get("objectSize")}
        _, nl = self._paragraph_bounds(pos)
        self.seq.insert(pos, _Char(IMAGE, image=obj_id))

    # ------------------------------------------------------------------ #
    # documents.get()
    # ------------------------------------------------------------------ #
    def to_json(self, tabs: bool = False) -> dict:
        """`tabs`: in the shape documents.get(includeTabsContent=True) returns
        (with `tab_id` set, the content sits in a tab inside an empty first tab)."""
        content, i = self._elements(0, set())
        inner = {
            "body": {"content": [{"startIndex": 0, "endIndex": 1, "sectionBreak": {}}] + content},
            "lists": copy.deepcopy(self.lists),
            "documentStyle": {
                "pageSize": {"width": {"magnitude": 612, "unit": "PT"}, "height": {"magnitude": 792, "unit": "PT"}},
                "marginLeft": {"magnitude": 72, "unit": "PT"}, "marginRight": {"magnitude": 72, "unit": "PT"},
            },
        }
        if not tabs:
            return {"documentId": self.document_id, "title": self.title, **inner}
        mine = {"tabProperties": {"tabId": self.tab_id or "t.0", "title": "Notes"}, "documentTab": inner}
        if not self.tab_id:
            return {"documentId": self.document_id, "title": self.title, "tabs": [mine]}
        empty = {"body": {"content": [
            {"startIndex": 0, "endIndex": 1, "sectionBreak": {}},
            {"startIndex": 1, "endIndex": 2, "paragraph": {
                "elements": [{"startIndex": 1, "endIndex": 2, "textRun": {"content": "\n", "textStyle": {}}}],
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}}}]}}
        first = {"tabProperties": {"tabId": "t.0", "title": "Tab 1"}, "documentTab": empty, "childTabs": [mine]}
        return {"documentId": self.document_id, "title": self.title, "tabs": [first]}

    def _elements(self, i: int, stop: set) -> tuple[list[dict], int]:
        out = []
        while i < len(self.seq) and self.seq[i].c not in stop:
            if self.seq[i].c == TABLE_START:
                el, i = self._table(i)
            else:
                el, i = self._paragraph(i)
            out.append(el)
        return out, i

    def _paragraph(self, i: int) -> tuple[dict, int]:
        start = i
        elements = []
        while self.seq[i].c != "\n":
            ch = self.seq[i]
            if ch.c == IMAGE:
                elements.append({"startIndex": i + 1, "endIndex": i + 2,
                                 "inlineObjectElement": {"inlineObjectId": ch.image}})
            elif elements and "textRun" in elements[-1] and elements[-1]["textRun"]["textStyle"] == ch.ts:
                elements[-1]["textRun"]["content"] += ch.c
                elements[-1]["endIndex"] += 1
            else:
                elements.append({"startIndex": i + 1, "endIndex": i + 2,
                                 "textRun": {"content": ch.c, "textStyle": dict(ch.ts)}})
            i += 1
        nl = self.seq[i]
        if elements and "textRun" in elements[-1]:
            elements[-1]["textRun"]["content"] += "\n"
            elements[-1]["endIndex"] += 1
        else:
            elements.append({"startIndex": i + 1, "endIndex": i + 2,
                             "textRun": {"content": "\n", "textStyle": dict(nl.ts)}})
        par = {"elements": elements, "paragraphStyle": dict(nl.ps)}
        if nl.bullet:
            par["bullet"] = dict(nl.bullet)
        return {"startIndex": start + 1, "endIndex": i + 2, "paragraph": par}, i + 1

    def _table(self, i: int) -> tuple[dict, int]:
        start = i
        i += 1
        rows = []
        while self.seq[i].c == ROW_START:
            row_start = i
            i += 1
            cells = []
            while self.seq[i].c == CELL_START:
                cell_start = i
                content, i = self._elements(i + 1, {CELL_START, ROW_START, TABLE_END})
                cells.append({"startIndex": cell_start + 1, "endIndex": i + 1, "content": content})
            rows.append({"startIndex": row_start + 1, "endIndex": i + 1, "tableCells": cells})
        assert self.seq[i].c == TABLE_END
        i += 1
        table = {"rows": len(rows), "columns": len(rows[0]["tableCells"]) if rows else 0, "tableRows": rows}
        if self.seq[start].image:  # test-only marker: the table would appear indented
            table.update(self.seq[start].image)
        return {"startIndex": start + 1, "endIndex": i + 1, "table": table}, i

    # ------------------------------------------------------------------ #
    def outline(self) -> list:
        """Readable summary: per paragraph (style, bullet level or None, text,
        text runs with styles); tables as nested lists of cells."""
        def walk(elements):
            out = []
            for el in elements:
                if "paragraph" in el:
                    p = el["paragraph"]
                    text = "".join(e.get("textRun", {}).get("content", "[image]") for e in p["elements"]).rstrip("\n")
                    runs = [(e["textRun"]["content"].rstrip("\n"), e["textRun"]["textStyle"])
                            for e in p["elements"] if "textRun" in e and e["textRun"]["content"].strip()]
                    level = p["bullet"]["nestingLevel"] if "bullet" in p else None
                    out.append((p["paragraphStyle"].get("namedStyleType"), level, text, runs))
                elif "table" in el:
                    out.append(("TABLE", [[walk(c["content"]) for c in r["tableCells"]]
                                          for r in el["table"]["tableRows"]]))
            return out
        return walk(self.to_json()["body"]["content"][1:])


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self, num_retries: int = 0):
        return self._fn()


class _Documents:
    def __init__(self, doc: FakeDoc):
        self._doc = doc

    def get(self, documentId: str, includeTabsContent: bool = False, **_):
        return _Call(lambda: self._doc.to_json(tabs=includeTabsContent))

    def batchUpdate(self, documentId: str, body: dict):
        def run():
            self._doc.apply(body["requests"])
            return {"replies": [{} for _ in body["requests"]]}
        return _Call(run)


class FakeDocsService:
    """Drop-in for `build("docs", "v1", ...)` in tests and dry runs."""

    def __init__(self, doc: FakeDoc | None = None):
        self.doc = doc or FakeDoc()

    def documents(self):
        return _Documents(self.doc)
