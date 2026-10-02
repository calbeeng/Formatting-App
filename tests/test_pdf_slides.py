"""Checks against the PowerPoint-exported slide deck sample."""

from conftest import GOLDEN, SLIDES, find

from notes2gdoc.config import Settings
from notes2gdoc.outline import render_outline
from notes2gdoc.parsers import parse_file


def test_detected_as_slides(slides):
    assert slides.layout == "slides"
    assert slides.page_count == 19


def test_title_slide_unticked(slides):
    first = [b for b in slides.blocks if b.page == 1]
    assert [b.text for b in first] == [
        "Corporate and Commercial Practice", "Insolvency Law and Practice", "Cross-Border Insolvency"]
    assert not any(b.selected for b in first)
    assert not any("Copyright" in b.text for b in first)  # footer removed


def test_slide_titles_are_headings_without_colour(slides):
    h = find(slides, "Introduction", "heading")
    assert h.style_key == "slide_title"
    assert h.text == "Introduction – what is an “international insolvency or restructuring”"
    for b in slides.blocks:
        if b.kind == "heading":
            assert all(r.color is None and not r.italic and not r.bold for r in b.runs)


def test_repeated_titles_merged(slides):
    titles = [b.text for b in slides.blocks if b.kind == "heading"]
    assert titles.count("UNCITRAL Model Law") == 1
    assert titles.count("Invoking Singapore processes in conjunction with Cross-Border Restructuring") == 1
    # Bullets from slide 10 still present under the merged heading
    assert find(slides, "adequately protected – Art 22.").page == 10


def test_bullet_levels(slides):
    assert find(slides, "Platform for:").level == 0
    assert find(slides, "granting of relief to assist").level == 1
    assert find(slides, "No requirement of reciprocity").level == 0
    assert find(slides, "No “ring-fencing”").level == 1


def test_superscript_kept(slides):
    b = find(slides, "Came into effect 23 May 2017")
    assert [r.text for r in b.runs if r.superscript] == ["rd"]
    assert b.text == "Came into effect 23 May 2017 (3rd Schedule, IRDA)"


def test_justified_words_rejoined(slides):
    b = find(slides, "Types of relief")
    assert b.text == "Types of relief – eg, stay against proceedings and execution; no transfer of assets."


def test_bold_italic_case_names(slides):
    b = find(slides, "Application of common law")
    styled = [r.text for r in b.runs if r.bold and r.italic]
    assert "Re Opti-Medix" in styled


def test_notice_slide_skipped_by_default(slides):
    notice = [b for b in slides.blocks if b.page == 19]
    assert notice and not any(b.selected for b in notice)


def test_notice_slide_toggle():
    from conftest import need
    need(SLIDES)
    doc = parse_file(SLIDES, Settings(skip_boilerplate_slides=False))
    assert all(b.selected for b in doc.blocks if b.page == 19)


def test_golden_outline(slides, settings):
    from conftest import need
    need(GOLDEN / "slides.outline.txt")
    expected = (GOLDEN / "slides.outline.txt").read_text(encoding="utf-8")
    actual = f"<!-- slides, 19 pages, {len(slides.blocks)} blocks -->\n" + render_outline(slides, settings)
    assert actual == expected
