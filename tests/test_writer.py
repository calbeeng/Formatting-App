"""The Google Docs writer, replayed against the in-memory fake Docs API.

The fake rejects out-of-range indexes and any delete request, so simply
running these appends checks the index arithmetic and the "never modify
existing content" rule.
"""

import json

import pytest
from conftest import DIVORCE, MA, SYLLABUS, parse_sample

from notes2gdoc.config import Settings
from notes2gdoc.gdocs import AppendJob, FakeDoc, FakeDocsService, PlaceholderImageHost, doc_id_from_url
from notes2gdoc.gdocs import dry_run as do_dry_run
from notes2gdoc.gdocs.target import bullet_preset
from notes2gdoc.model import Block, Document, Run
from notes2gdoc.parsers import parse_file

EXISTING = [("My exam notes", "TITLE"), ("Some existing text", "NORMAL_TEXT")]


def append(blocks, doc=None, settings=None):
    doc = doc or FakeDoc(list(EXISTING))
    job = AppendJob(FakeDocsService(doc), "doc", settings or Settings(), PlaceholderImageHost(),
                    sleep=lambda *_: None)
    result = job.run(blocks)
    return doc, result


def all_requests(result):
    return [r for batch in result.batches for r in batch]


def paras(doc):
    return [p for p in doc.outline() if p[0] != "TABLE"]


def test_existing_content_untouched_and_separator(syllabus_doc):
    doc, result = append(syllabus_doc.blocks)
    out = doc.outline()
    assert out[0][:3] == ("TITLE", None, "My exam notes")
    assert out[1][:3] == ("NORMAL_TEXT", None, "Some existing text")
    assert out[2][:3] == ("NORMAL_TEXT", None, "")            # the blank separator line
    assert out[3][2].startswith("Cases and topics marked")     # first appended block
    kinds = {next(iter(r)) for r in all_requests(result)}
    assert not kinds & {"deleteContentRange", "replaceAllText", "replaceNamedRangeContent"}
    # Only inserts happen at or after the end of the original text
    original_end = len("My exam notes\nSome existing text")
    for r in all_requests(result):
        body = next(iter(r.values()))
        idx = body.get("location", {}).get("index") or body.get("range", {}).get("startIndex")
        if idx is not None:
            assert idx >= original_end, r


def test_headings_bullets_and_styles(syllabus_doc):
    doc, _ = append(syllabus_doc.blocks)
    ps = paras(doc)
    by_text = {p[2]: p for p in ps}
    assert by_text["1. LIQUIDATION"][0] == "HEADING_1"
    assert by_text["(a) Overview of Liquidation"][0] == "HEADING_2"
    assert by_text["(ii) Presumption of Insolvency"][0] == "HEADING_3"
    assert by_text["Part 8 of the IRDA"][1] == 0                  # bullet, level 0
    assert not any("\t" in p[2] for p in ps)                       # tabs consumed by bullets
    assert by_text["Extra Readings:"][1] is None                   # not a bullet
    # Inline styles: blue + italic case name
    runs = by_text[next(t for t in by_text if t.startswith("Contingent vs prospective"))][3]
    italic = [t for t, st in runs if st.get("italic")]
    assert italic == ["Re People’s Parkway Development Pte Ltd"]
    assert all("foregroundColor" in st for _, st in runs)
    # Never sets fonts or sizes
    for _, _, _, rs in ps:
        assert all("weightedFontFamily" not in st and "fontSize" not in st for _, st in rs)


def test_strip_colour(syllabus_doc):
    doc, result = append(syllabus_doc.blocks, settings=Settings(strip_colour=True))
    for r in all_requests(result):
        if "updateTextStyle" in r:
            assert "foregroundColor" not in r["updateTextStyle"]["textStyle"]


def test_nested_bullets_and_superscript():
    blocks = [
        Block("heading", [Run("Topic")], style_key="slide_title"),
        Block("bullet", [Run("Came into effect (3"), Run("rd", superscript=True), Run(" Schedule)")], level=0),
        Block("bullet", [Run("Sub point")], level=1),
        Block("bullet", [Run("Sub sub point")], level=2),
        Block("paragraph", [Run("After the list")]),
    ]
    doc, _ = append(blocks)
    ps = paras(doc)[3:]
    assert [(p[0], p[1], p[2]) for p in ps] == [
        ("HEADING_2", None, "Topic"),
        ("NORMAL_TEXT", 0, "Came into effect (3rd Schedule)"),
        ("NORMAL_TEXT", 1, "Sub point"),
        ("NORMAL_TEXT", 2, "Sub sub point"),
        ("NORMAL_TEXT", None, "After the list"),
    ]
    assert ("rd", {"baselineOffset": "SUPERSCRIPT"}) in ps[1][3]


def test_inherited_bullet_and_style_are_cleared():
    # Existing doc ends with a bold bulleted heading-styled paragraph
    doc = FakeDoc([("Intro", "HEADING_1")])
    doc.seq[-1].bullet = {"listId": "x", "nestingLevel": 0}
    for ch in doc.seq[:-1]:
        ch.ts["bold"] = True
    doc, _ = append([Block("paragraph", [Run("Plain text")])], doc=doc)
    ps = paras(doc)
    assert ps[0][1] == 0 and ps[0][0] == "HEADING_1"   # original paragraph untouched
    assert ps[-1][:3] == ("NORMAL_TEXT", None, "Plain text")
    assert all(not st.get("bold") for _, st in ps[-1][3])


def test_empty_doc_has_no_separator():
    doc, _ = append([Block("paragraph", [Run("First")])], doc=FakeDoc())
    assert [p[2] for p in paras(doc)] == ["First"]


def test_doc_ending_in_blank_line_reuses_it():
    doc = FakeDoc([("Notes", "NORMAL_TEXT"), ("", "NORMAL_TEXT")])
    doc, _ = append([Block("paragraph", [Run("New")])], doc=doc)
    assert [p[2] for p in paras(doc)] == ["Notes", "", "New"]


def test_tables_and_pictures():
    ma = parse_sample(MA)
    doc, result = append(ma.blocks)
    tables = [p for p in doc.outline() if p[0] == "TABLE"]
    assert len(tables) == 2
    features = tables[0][1]
    assert len(features) == 8 and len(features[0]) == 3
    assert features[0][0][0][2] == "Features"
    assert features[7][2][0][2] == "Section 18A of Employment Act 1968"
    locked = tables[1][1]
    left_cell = locked[1][0]
    assert len(left_cell) == 5 and all(p[1] == 0 for p in left_cell)   # bullets in the cell
    # Pictures: inserted with a size that fits the page (468pt usable width)
    images = [r["insertInlineImage"] for r in all_requests(result) if "insertInlineImage" in r]
    assert len(images) == 3
    assert all(i["objectSize"]["width"]["magnitude"] <= 468 for i in images)
    assert sum(1 for p in paras(doc) if p[2] == "[image]") == 3


def test_merged_cells():
    d = parse_sample(DIVORCE)
    _, result = append(d.blocks)
    merges = [r["mergeTableCells"] for r in all_requests(result) if "mergeTableCells" in r]
    assert any(m["tableRange"]["columnSpan"] == 2 for m in merges)


def test_bullet_preset_from_target_doc():
    doc = FakeDoc([("a", "NORMAL_TEXT")]).to_json()
    assert bullet_preset(doc) == "BULLET_DISC_CIRCLE_SQUARE"
    doc["lists"] = {"L": {"listProperties": {"nestingLevels": [{"glyphSymbol": "❖"}]}}}
    doc["body"]["content"][1]["paragraph"]["bullet"] = {"listId": "L", "nestingLevel": 0}
    assert bullet_preset(doc) == "BULLET_DIAMONDX_ARROW3D_SQUARE"


def test_retries_when_rate_limited():
    class Resp:
        status = 429

    class RateLimited(Exception):
        resp = Resp()

    svc = FakeDocsService(FakeDoc())
    real = svc.documents

    class Flaky:
        calls = 0

        def get(self, **kw):
            return real().get(**kw)

        def batchUpdate(self, **kw):
            call = real().batchUpdate(**kw)

            class FailOnce:  # like a real request object, it can be executed again
                def execute(self, **_):
                    Flaky.calls += 1
                    if Flaky.calls == 1:
                        raise RateLimited()
                    return call.execute()
            return FailOnce()

    svc.documents = lambda: Flaky()
    sleeps = []
    AppendJob(svc, "d", Settings(), PlaceholderImageHost(), sleep=sleeps.append).run(
        [Block("paragraph", [Run("Hi")])])
    assert sleeps and [p[2] for p in svc.doc.outline()] == ["Hi"]


def test_doc_id_from_url():
    url = "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit?tab=t.0"
    assert doc_id_from_url(url) == "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    assert doc_id_from_url("1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789") == "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    assert doc_id_from_url("https://example.com/foo") is None


def test_dry_run_writes_json(tmp_path, syllabus_doc):
    out = tmp_path / "plan.json"
    do_dry_run(syllabus_doc, "doc", Settings(), str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["batches"] and data["preview"][0][2].startswith("Cases and topics marked")


@pytest.fixture(scope="module")
def syllabus_doc():
    return parse_sample(SYLLABUS)


# --------------------------------------------------------------------------- #
# Inserting at the end of a chosen section
# --------------------------------------------------------------------------- #
from notes2gdoc.gdocs.target import headings as doc_headings  # noqa: E402
from notes2gdoc.model import Image, Table, TableCell  # noqa: E402

SECTIONED = [
    ("1. LIQUIDATION", "HEADING_1"),
    ("Existing liquidation notes", "NORMAL_TEXT"),
    ("(a) Overview", "HEADING_2"),
    ("Existing overview notes", "NORMAL_TEXT"),
    ("2. BANKRUPTCY", "HEADING_1"),
    ("Existing bankruptcy notes", "NORMAL_TEXT"),
]


def append_after(blocks, heading_text, doc=None):
    doc = doc or FakeDoc(list(SECTIONED))
    h = next(h for h in doc_headings(doc.to_json()) if h.text == heading_text)
    job = AppendJob(FakeDocsService(doc), "doc", Settings(), PlaceholderImageHost(), sleep=lambda *_: None)
    job.run(blocks, after_heading=h)
    return doc


def mixed_blocks():
    table = Table(1, 2, [TableCell(0, 0, [Block("paragraph", [Run("Cell A")])]),
                         TableCell(0, 1, [Block("bullet", [Run("Cell B")])])])
    return [
        Block("heading", [Run("New topic")], style_key="alpha"),
        Block("bullet", [Run("New point")]),
        Block("table", table=table),
        Block("image", image=Image(b"\x89PNG fake", 400, 200, "Diagram")),
        Block("paragraph", [Run("Closing line")]),
    ]


def texts(doc):
    out = []
    for p in doc.outline():
        out.append("TABLE" if p[0] == "TABLE" else (p[0], p[1], p[2]))
    return out


def test_insert_at_end_of_level1_section():
    doc = append_after(mixed_blocks(), "1. LIQUIDATION")
    t = texts(doc)
    assert t[:4] == [
        ("HEADING_1", None, "1. LIQUIDATION"),
        ("NORMAL_TEXT", None, "Existing liquidation notes"),
        ("HEADING_2", None, "(a) Overview"),
        ("NORMAL_TEXT", None, "Existing overview notes"),
    ]
    assert t[4] == ("NORMAL_TEXT", None, "")                 # blank separator
    assert t[5] == ("HEADING_2", None, "New topic")
    assert t[6] == ("NORMAL_TEXT", 0, "New point")
    assert "TABLE" in t and ("NORMAL_TEXT", None, "[image]") in t
    i = t.index(("HEADING_1", None, "2. BANKRUPTCY"))
    assert t[i - 1] == ("NORMAL_TEXT", None, "Closing line")  # new notes end right before the next section
    assert t[i + 1] == ("NORMAL_TEXT", None, "Existing bankruptcy notes")
    assert len(t) == i + 2                                     # nothing added after it


def test_insert_after_subheading_goes_before_next_higher_heading():
    doc = append_after([Block("paragraph", [Run("More overview")])], "(a) Overview")
    t = texts(doc)
    i = t.index(("HEADING_1", None, "2. BANKRUPTCY"))
    assert t[i - 3:i] == [("NORMAL_TEXT", None, "Existing overview notes"),
                          ("NORMAL_TEXT", None, ""),
                          ("NORMAL_TEXT", None, "More overview")]


def test_insert_after_last_section_goes_to_end():
    doc = append_after([Block("paragraph", [Run("Tail")])], "2. BANKRUPTCY")
    assert texts(doc)[-2:] == [("NORMAL_TEXT", None, ""), ("NORMAL_TEXT", None, "Tail")]


def test_section_ending_with_a_table():
    doc = FakeDoc([("1. A", "HEADING_1"), ("intro", "NORMAL_TEXT"), ("2. B", "HEADING_1")])
    # Put a table at the end of section 1 (after "intro"), the way Docs would
    intro_end = len("1. A\nintro")
    doc.apply([{"insertTable": {"rows": 1, "columns": 1, "location": {"index": intro_end}}}])
    doc = append_after([Block("paragraph", [Run("Added")])], "1. A", doc=doc)
    t = texts(doc)
    i = t.index(("HEADING_1", None, "2. B"))
    assert t[i - 1] == ("NORMAL_TEXT", None, "Added")
    assert "TABLE" in t[:i]


def test_nothing_indented_after_a_list_and_table():
    """Regression: a table right after a sub-bullet came out indented, and so
    did the next heading (Google adds an indent when it removes a bullet)."""
    table = Table(1, 2, [TableCell(0, 0, [Block("paragraph", [Run("A")])]),
                         TableCell(0, 1, [Block("paragraph", [Run("B")])])])
    blocks = [
        Block("heading", [Run("SPA: Consideration")], style_key="slide_title"),
        Block("bullet", [Run("Contingent value rights")], level=0),
        Block("bullet", [Run("Buyer and Seller share the risk")], level=1),
        Block("table", table=table),
        Block("heading", [Run("SPA: Conditions Precedent")], style_key="slide_title"),
        Block("bullet", [Run("Purpose")], level=0),
    ]
    doc, _ = append(blocks)
    content = doc.to_json()["body"]["content"]
    tables = [c["table"] for c in content if "table" in c]
    assert tables and "inheritedIndent" not in tables[0]
    for c in content:
        if "paragraph" in c:
            assert _indent(c["paragraph"]) == 0, c
    by_text = {p[2]: p for p in paras(doc)}
    assert by_text["SPA: Conditions Precedent"][:2] == ("HEADING_2", None)


def test_ma_deck_has_no_stray_indents():
    ma = parse_sample(MA)
    doc, _ = append(ma.blocks)
    for c in doc.to_json()["body"]["content"]:
        if "paragraph" in c:
            text = "".join(e.get("textRun", {}).get("content", "") for e in c["paragraph"]["elements"])
            if text.startswith("“If prior to completion"):  # the one deliberately indented quote
                assert c["paragraph"]["paragraphStyle"]["indentStart"]["magnitude"] == 36
            else:
                assert _indent(c["paragraph"]) == 0, text
        if "table" in c:
            assert "inheritedIndent" not in c["table"]


def _para_styles(doc):
    return [(c["paragraph"]["paragraphStyle"], "".join(e.get("textRun", {}).get("content", "[img]")
             for e in c["paragraph"]["elements"]).strip())
            for c in doc.to_json()["body"]["content"] if "paragraph" in c]


def test_justified_centred_and_list_indent():
    blocks = [
        Block("heading", [Run("SPA: Termination Rights")], style_key="slide_title"),
        Block("bullet", [Run("Termination for MAC")], level=0),
        Block("paragraph", [Run("“If prior to completion…”", italic=True)], level=1),
        Block("bullet", [Run("Gives buyer ability to walk")], level=1),
        Block("image", image=Image(b"\x89PNG", 300, 100, "Diagram")),
    ]
    doc, _ = append(blocks)
    styles = {text: st for st, text in _para_styles(doc)}
    assert styles["SPA: Termination Rights"]["alignment"] == "JUSTIFIED"
    assert styles["Termination for MAC"]["alignment"] == "JUSTIFIED"
    quote = styles["“If prior to completion…”"]
    assert quote["indentStart"]["magnitude"] == 36 and quote["indentFirstLine"]["magnitude"] == 36
    assert styles["[img]"]["alignment"] == "CENTER" and "indentStart" not in styles["[img]"]
    # Existing content keeps its own alignment
    assert "alignment" not in styles["My exam notes"]


def test_justify_can_be_turned_off():
    doc, _ = append([Block("paragraph", [Run("Plain")])], settings=Settings(justify_text=False))
    styles = {text: st for st, text in _para_styles(doc)}
    assert "alignment" not in styles["Plain"]


def test_tables_full_width_with_source_proportions():
    table = Table(1, 2, [TableCell(0, 0, [Block("paragraph", [Run("A")])]),
                         TableCell(0, 1, [Block("paragraph", [Run("B")])])], col_widths=[100, 300])
    doc, _ = append([Block("table", table=table)])
    t = next(c["table"] for c in doc.to_json()["body"]["content"] if "table" in c)
    assert t["columnWidths"] == {0: 117.0, 1: 351.0}   # 468pt page width split 1:3
    ma = parse_sample(MA)
    doc, _ = append(ma.blocks)
    for c in doc.to_json()["body"]["content"]:
        if "table" in c:
            assert abs(sum(c["table"]["columnWidths"].values()) - 468) < 1


def _indent(paragraph):
    return paragraph["paragraphStyle"].get("indentStart", {}).get("magnitude", 0)


def test_table_cells_and_table_line_flush_left():
    table = Table(1, 1, [TableCell(0, 0, [Block("paragraph", [Run("1. Selection of Assets / Liabilities")])])])
    blocks = [Block("bullet", [Run("Execution risks")]), Block("table", table=table)]
    doc, result = append(blocks)
    content = doc.to_json()["body"]["content"]
    t_i = next(i for i, c in enumerate(content) if "table" in c)
    cell_para = content[t_i]["table"]["tableRows"][0]["tableCells"][0]["content"][0]["paragraph"]
    assert cell_para["paragraphStyle"]["indentStart"]["magnitude"] == 0
    assert cell_para["paragraphStyle"]["indentFirstLine"]["magnitude"] == 0
    before = content[t_i - 1]["paragraph"]
    assert "bullet" not in before and before["paragraphStyle"]["indentStart"]["magnitude"] == 0
    assert "inheritedIndent" not in content[t_i]["table"]


def test_highlight_cell_colour_and_blank_lines():
    table = Table(1, 2, [TableCell(0, 0, [Block("paragraph", [Run("Header")])], background="#9CC2E5"),
                         TableCell(0, 1, [Block("paragraph", [Run("Plain")])])])
    blocks = [
        Block("paragraph", [Run("(1) Purpose", bold=True, highlight="#FFFF00"), Run(": text")]),
        Block("paragraph", spacer=True),
        Block("paragraph", [Run("After a blank line")]),
        Block("table", table=table),
    ]
    doc, result = append(blocks)
    ps = paras(doc)
    texts = [p[2] for p in ps]
    i = texts.index("(1) Purpose: text")
    assert texts[i + 1] == "" and texts[i + 2] == "After a blank line"   # blank line kept
    runs = dict(ps[i][3])
    assert runs["(1) Purpose"]["backgroundColor"]["color"]["rgbColor"]["red"] == 1.0
    t = next(c["table"] for c in doc.to_json()["body"]["content"] if "table" in c)
    assert set(t["cellBackgrounds"]) == {(0, 0)}
    # Strip colour also strips highlights and cell shading
    doc2, result2 = append(blocks, settings=Settings(strip_colour=True))
    reqs = all_requests(result2)
    assert not any("updateTableCellStyle" in r for r in reqs)
    for r in reqs:
        if "updateTextStyle" in r:
            style = r["updateTextStyle"]["textStyle"]
            assert "backgroundColor" not in style and "foregroundColor" not in style


def test_paragraphs_flush_left_by_default():
    """The target doc's Normal style may have a hanging indent (18pt/36pt);
    appended headings and paragraphs start at the margin anyway."""
    blocks = [
        Block("heading", [Run("Bonds")], style_key="word_h3"),
        Block("paragraph", [Run("(1) Defining (debt obligations + security): Instruments creating …")]),
        Block("bullet", [Run("The issuer promises to repay")]),
    ]
    doc, _ = append(blocks)
    styles = {text: st for st, text in _para_styles(doc)}
    for text in ("Bonds", "(1) Defining (debt obligations + security): Instruments creating …"):
        assert styles[text]["indentStart"]["magnitude"] == 0
        assert styles[text]["indentFirstLine"]["magnitude"] == 0
    assert "indentStart" not in styles["The issuer promises to repay"] or \
        styles["The issuer promises to repay"]["indentStart"]["magnitude"] == 0  # bullets: list decides
    doc2, _ = append(blocks, settings=Settings(flush_left=False))
    styles2 = {text: st for st, text in _para_styles(doc2)}
    assert "indentStart" not in styles2["Bonds"]


def _body(doc):
    return doc.to_json()["body"]["content"]


def test_table_straight_under_a_heading():
    """No blank line between a heading (or plain text) and the table under it;
    after a bullet the blank line stays, so the table isn't indented."""
    def table():
        return Table(1, 2, [TableCell(0, 0, [Block("paragraph", [Run("A")])]),
                            TableCell(0, 1, [Block("paragraph", [Run("B")])])])
    blocks = [Block("heading", [Run("Re BKR")], style_key="slide_title"), Block("table", table=table()),
              Block("bullet", [Run("A point")], level=0), Block("table", table=table())]
    doc, _ = append(blocks)
    content = _body(doc)
    first = next(i for i, c in enumerate(content) if "table" in c)
    above = content[first - 1]["paragraph"]
    assert "".join(e["textRun"]["content"] for e in above["elements"]).strip() == "Re BKR"
    assert above["paragraphStyle"]["namedStyleType"] == "HEADING_2"
    # the line under the table is plain, not a second heading
    below = content[first + 1]["paragraph"]
    assert below["paragraphStyle"]["namedStyleType"] == "NORMAL_TEXT"
    second = [i for i, c in enumerate(content) if "table" in c][1]
    gap = content[second - 1]["paragraph"]
    assert not "".join(e["textRun"]["content"] for e in gap["elements"]).strip()
    assert "inheritedIndent" not in content[second]["table"]


def test_typed_numbers_hang_like_a_list():
    item = Block("paragraph", [Run("2. Mandatory waiting period of 3 weeks")], hanging=True)
    sub = Block("paragraph", [Run("a. Through OPGO")], hanging=True, level=1)
    doc, _ = append([item, sub])
    ps = [c["paragraph"] for c in _body(doc) if "paragraph" in c]
    p2 = next(p for p in ps if "Mandatory" in "".join(e["textRun"]["content"] for e in p["elements"]))
    pa = next(p for p in ps if "OPGO" in "".join(e["textRun"]["content"] for e in p["elements"]))
    assert "".join(e["textRun"]["content"] for e in p2["elements"]).startswith("2.\tMandatory")
    st = p2["paragraphStyle"]
    assert (st["indentFirstLine"]["magnitude"], st["indentStart"]["magnitude"]) == (18, 36)
    st = pa["paragraphStyle"]
    assert (st["indentFirstLine"]["magnitude"], st["indentStart"]["magnitude"]) == (54, 72)
