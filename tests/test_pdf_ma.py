"""Checks against the M&A slide deck: flowcharts, tables, two columns,
section divider slides and subtitles."""

from conftest import GOLDEN, find

from notes2gdoc.outline import render_outline


def blocks_on(doc, page):
    return [b for b in doc.blocks if b.page == page]


def test_road_map_is_picture_only(ma):
    page = blocks_on(ma, 2)
    assert [b.kind for b in page] == ["heading", "image"]
    assert page[0].text == "M&A Road Map"
    pic = page[1]
    assert pic.image.png.startswith(b"\x89PNG")
    assert "Identify the target" in pic.image.alt  # text kept as alt text


def test_road_map_text_below_when_enabled(ma, monkeypatch):
    from notes2gdoc.parsers import parse_file
    from notes2gdoc.parsers.pdf import structure
    from conftest import MA, need
    need(MA)

    monkeypatch.setattr(structure, "DIAGRAM_TEXT_BELOW", True)
    page = [b for b in parse_file(MA).blocks if b.page == 2]
    bullets = [(b.level, b.text) for b in page[2:]]
    assert bullets[:4] == [
        (0, "Identify the target"),
        (0, "Pre-Acquisition"),
        (1, "Preliminary documentation"),
        (1, "Due diligence"),
    ]
    assert (0, "Negotiations / Definitive Documentation") in bullets
    assert (0, "Completion (legal transfer of shares / business)") in bullets


def test_white_text_has_no_colour(ma):
    def runs(blocks):
        for b in blocks:
            yield from b.runs
            if b.table:
                for c in b.table.cells:
                    yield from runs(c.blocks)
    assert not any(r.color == "#FFFFFF" for r in runs(ma.blocks))


def test_dash_line_continues_title(ma):
    titles = [b.text for b in ma.blocks if b.kind == "heading"]
    assert "Methods of Acquisition - Acquisition of Shares" in titles
    assert "Methods of Acquisition - Acquisition of Business" in titles
    assert "Methods of Acquisition" in titles  # slide 5, then slide 6 merges into it


def test_section_divider_slides(ma):
    for text in ("Pre-Acquisition Steps", "Sale and Purchase Agreement (SPA) Essentials"):
        b = find(ma, text, "heading")
        assert b.style_key == "section_title"


def test_bold_line_leads_its_bullets(ma):
    # A bold line in body-sized text is a main point; the slide's bullets nest under it
    b = find(ma, "Some questions that could impact deal structure")
    assert b.kind == "bullet" and b.level == 0
    assert find(ma, "Does the Buyer only want to acquire").level == 1


def test_comparison_table(ma):
    t = next(b for b in blocks_on(ma, 6) if b.kind == "table").table
    assert (t.n_rows, t.n_cols) == (8, 3)
    assert [t.cell(0, c).text for c in range(3)] == ["Features", "Share Acquisition", "Business Acquisition"]
    assert t.cell(4, 1).text == "May not avoid (unless there is drag-along right)"
    assert t.cell(7, 2).text == "Section 18A of Employment Act 1968"


def test_table_with_bullets_in_cells(ma):
    t = next(b for b in blocks_on(ma, 14) if b.kind == "table").table
    assert (t.n_rows, t.n_cols) == (2, 2)
    left = t.cell(1, 0).blocks
    assert all(b.kind == "bullet" for b in left)
    assert left[0].text == ("Actual level of assets and liabilities not known as at completion. "
                            "Price calculated using estimated level of assets and liabilities as at completion")
    assert len(left) == 5 and len(t.cell(1, 1).blocks) == 4


def test_two_columns_read_separately(ma):
    page = [b for b in blocks_on(ma, 9) if b.kind == "bullet"]
    texts = [(b.level, b.text) for b in page]
    assert texts.index((0, "Purpose of conducting due diligence")) < texts.index(
        (0, "Possible methods of dealing with negative revelations"))
    assert (1, "Legal") in texts and (1, "Walking away") in texts
    assert not any("•" in t for _, t in texts)


def test_golden_outline(ma, settings):
    from conftest import need
    need(GOLDEN / "ma.outline.txt")
    expected = (GOLDEN / "ma.outline.txt").read_text(encoding="utf-8")
    actual = f"<!-- slides, 24 pages, {len(ma.blocks)} blocks -->\n" + render_outline(ma, settings)
    assert actual == expected
