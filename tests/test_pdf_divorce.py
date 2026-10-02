"""Spot checks against the Divorce slide deck (Wingdings bullets, loose slide
numbers, merged table cells, a flowchart, side-by-side boxes)."""

import re

from conftest import find


def test_wingdings_bullets(divorce):
    b = find(divorce, "Ad-hoc Presidents")
    assert not b.text.startswith("Ø")
    assert not any(x.text.startswith("Ø") for x in divorce.blocks)


def test_no_loose_slide_numbers(divorce):
    assert not any(re.fullmatch(r"[\d ]+", b.text) for b in divorce.blocks)


def test_bullet_levels_are_sane(divorce):
    assert max(b.level for b in divorce.blocks if b.kind == "bullet") <= 2


def test_merged_header_cell(divorce):
    t = next(b for b in divorce.blocks if b.kind == "table" and "CIVIL COURTS" in b.text).table
    merged = t.cell(1, 0)
    assert merged.colspan == 2 and "Welfare of children paramount concern." in merged.text
    assert t.cell(1, 1) is None


def test_flowchart_is_picture(divorce):
    page = [b for b in divorce.blocks if b.page == 14]
    pic = next(b for b in page if b.kind == "image")
    assert "Settlement" in pic.image.alt  # the outlined "Settlement" box is part of it
    assert not any(b.text == "Settlement" for b in page if b.kind != "image")


def test_side_by_side_boxes_are_not_a_picture(divorce):
    page = [b for b in divorce.blocks if b.page == 18]
    assert not any(b.kind == "image" for b in page)
    dma = find(divorce, "DMA (Divorce")
    assert dma.text == "DMA (Divorce by Mutual Agreement) from 1 July 2024"
    assert dma.numbered and dma.level == 1   # "a. … e." -> lettered numbered list
