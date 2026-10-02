"""Checks against Samples/Syllabus.pdf (a Word-generated reading list)."""

from conftest import GOLDEN, find

from notes2gdoc.outline import render_outline

BLUE = "#00B0F0"


def headings(doc):
    return [(b.style_key, b.text) for b in doc.blocks if b.kind == "heading"]


def test_layout(syllabus):
    assert syllabus.layout == "document"
    assert syllabus.page_count == 13
    assert not syllabus.warnings


def test_heading_levels(syllabus):
    hs = headings(syllabus)
    assert hs[0] == ("decimal", "1. LIQUIDATION")
    assert ("alpha", "(a) Overview of Liquidation") in hs
    assert ("roman", "(ii) Presumption of Insolvency") in hs
    assert ("decimal", "3. JUDICIAL MANAGEMENT") in hs  # two spans with no space between
    assert sum(1 for k, _ in hs if k == "decimal") == 6
    assert len(hs) == 65


def test_roman_vs_letter_i(syllabus):
    assert find(syllabus, "(i) Remuneration").style_key == "roman"       # under (f), (ii) follows
    assert find(syllabus, "(i) Provable Debts").style_key == "roman"     # under (g), before (h)
    assert find(syllabus, "(i) Dissolution", "heading").style_key == "alpha"  # follows (h)
    assert find(syllabus, "(i) Inability to pay debts").style_key == "roman"


def test_furniture_removed(syllabus):
    text = "\n".join(b.text for b in syllabus.blocks)
    for junk in ("Reading List & Syllabus", "SILE Part B", "| Page", "* * *"):
        assert junk not in text
    # "Insolvency & Corporate Restructuring" is a running header; only the
    # (misspelt) title-block line on page 1 should remain.
    assert "Insolvency & Corporate Restructuring" not in text


def test_title_block_unticked(syllabus):
    first = syllabus.blocks[:3]
    assert [b.text for b in first] == [
        "SYLLABUS & READING LIST",
        "Insolvency & Corporate Restructuing",
        "Insolvency, Restructuring and Dissolution Act 2018 (“IRDA”)",
    ]
    assert not any(b.selected for b in first)
    assert all(b.selected for b in syllabus.blocks[3:])


def test_notice_box_is_paragraphs_with_underline(syllabus):
    box = find(syllabus, "Cases and topics marked")
    assert box.kind == "paragraph"
    assert "in-depth knowledge on the topic." in box.text  # wrapped lines merged
    underlined = [r.text for r in box.runs if r.underline]
    assert underlined == ["NON-ESSENTIAL", "NOT"]
    assert any(r.color == BLUE and "in blue" in r.text for r in box.runs)


def test_bullets_glyph_removed_and_wrapped(syllabus):
    b = find(syllabus, "Cashflow insolvency test")
    assert b.kind == "bullet" and b.level == 0
    assert b.text == (
        "Cashflow insolvency test vs Balance sheet insolvency test: "
        "Sun Electric Power Pte Ltd v RCMA Asia Pte Ltd [2021] SGCA 60"
    )
    assert not any(b.text.startswith(g) for g in "•")


def test_blue_text_survives(syllabus):
    b = find(syllabus, "Contingent vs prospective creditor")
    assert all(r.color == BLUE for r in b.runs)
    assert any(r.italic for r in b.runs)
    h = find(syllabus, "(ii) Principles applicable", "heading")
    assert all(r.color == BLUE for r in h.runs)
    # Mixed: black "(c)" marker, blue heading text
    h = find(syllabus, "Schemes of Arrangement proposed", "heading")
    assert h.runs[0].color is None and h.runs[-1].color == BLUE


def test_heading_inline_italic_kept_bold_dropped(syllabus):
    h = find(syllabus, "Pari Passu", "heading")
    assert [r.text for r in h.runs if r.italic] == ["Pari Passu"]
    assert not any(r.bold for r in h.runs)  # uniform bold comes from the heading style


def test_cross_page_continuation(syllabus):
    b = find(syllabus, "Avoidance Law in Judicial Management")
    assert b.text.endswith("by Wee Meng Seng, Associate Professor, Faculty of Law, National University of Singapore")
    # "Professor..." must not appear as its own block
    assert not any(x.text.startswith("Professor, Faculty") for x in syllabus.blocks)


def test_extra_readings_after_page_break_is_new_paragraph(syllabus):
    # Page 11 starts with "Extra Readings:" after a gap; it must not be glued to
    # the last bullet of page 10.
    b = find(syllabus, "Automatic interim moratorium")
    assert b.text == "Automatic interim moratorium: Section 64 of the IRDA"


def test_dash_at_wrap_is_not_a_bullet(syllabus):
    b = find(syllabus, "Insolvency set-off: Law and Practice")
    assert b.text.endswith("Chapter VI [1905 – 2101]")


def test_extra_readings_label(syllabus):
    b = find(syllabus, "Extra Readings")
    assert b.kind == "paragraph" and b.selected
    assert all(r.color == BLUE for r in b.runs)


def test_golden_outline(syllabus, settings):
    from conftest import need
    need(GOLDEN / "syllabus.outline.txt")
    expected = (GOLDEN / "syllabus.outline.txt").read_text(encoding="utf-8")
    actual = f"<!-- document, 13 pages, {len(syllabus.blocks)} blocks -->\n" + render_outline(syllabus, settings)
    assert actual == expected
