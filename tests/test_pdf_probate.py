"""Checks against the Probate & Succession Planning syllabus: an outline typed
as 1. / (a) / (i) with wide ("1.5 lines") spacing."""

import pytest
from conftest import SAMPLES, find, parse_sample

from notes2gdoc.lists import display_labels

PROBATE = next(SAMPLES.glob("Probate*Syllabus*.pdf"), None)


@pytest.fixture(scope="module")
def probate(settings):
    return parse_sample(PROBATE, settings)


def label(doc, text):
    return display_labels(doc.blocks).get(id(find(doc, text)))


def test_item_at_top_of_page_is_its_own_item(probate):
    # "3. Will formalities" ends page 1 and "(a) Formalities" starts page 2
    assert find(probate, "Will formalities").text == "Will formalities"
    assert label(probate, "Will formalities") == "3."
    assert label(probate, "Formalities (Section 5 WA)") == "a."
    assert find(probate, "Practical pointers after execution").text == "Practical pointers after execution"
    assert label(probate, "Safekeeping of wills") == "i."


def test_letters_and_romans_join_the_numbered_list(probate):
    assert label(probate, "Duties and liabilities of solicitors") == "d."
    assert label(probate, "Cheo Yeoh & Associates LLC v AEL [2015] 4 SLR 325") == "ii."
    assert label(probate, "Testamentary capacity") == "4."
    assert label(probate, "Testimonium clause") == "i."   # the letter after (h)
    typed = [b.text for b in probate.blocks if b.kind == "paragraph" and b.text.startswith("(")]
    assert typed == []


def test_list_items_are_not_title_block(probate):
    assert [b.text for b in probate.blocks if not b.selected] == [
        "PRIVATE CLIENT PRACTICE", "PROBATE & SUCCESSION PLANNING", "SYLLABUS"]


def test_wide_line_spacing_still_wraps(probate):
    assert find(probate, "NOTE: Content in black font").text.endswith("Knowledge.")
    b = find(probate, "CTCs of digitally verifiable")
    assert b.kind == "bullet" and b.text.endswith("Singapore are not required")
    assert find(probate, "Colin Tan Boon Chwee").text.endswith("(2025)")


def test_bullet_under_the_last_item_nests_like_the_others(probate):
    earlier = find(probate, "Costs of proceeding (P. 22, r. 4")
    final = find(probate, "Sections 22 and 23 MCA")          # under "(iv)", the list's last item
    assert final.kind == "bullet" and final.level == earlier.level
    assert find(probate, "G. Raman").level == 0               # the reference lists afterwards are untouched
