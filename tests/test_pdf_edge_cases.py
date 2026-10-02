"""Small synthetic PDFs for cases the samples don't cover."""

import pymupdf as fitz
import pytest

from notes2gdoc.model import Block, Run
from notes2gdoc.parsers import NoTextLayerError, parse_file
from notes2gdoc.parsers.pdf.structure import join_line, resolve_line_hyphens
from notes2gdoc.parsers.pdf.extract import Line, Piece


def _line(text):
    return Line(page=0, x0=0, y0=0, x1=0, y1=0, size=11,
                pieces=[Piece(text, False, False, False, False, False, None, 11, 0, 0)])


def test_hyphenated_line_end_rejoined():
    # "insolvency" is used elsewhere, so "insol-" + "vency" is a broken word
    b = Block("paragraph", [Run("the insol-")])
    join_line(b, _line("vency of the company"))
    resolve_line_hyphens([b, Block("paragraph", [Run("Insolvency Act")])])
    assert b.text == "the insolvency of the company"


def test_line_end_hyphen_kept_when_word_is_hyphenated():
    # "decisionspecific" isn't a word used anywhere, so the hyphen is real
    b = Block("paragraph", [Run("time-specific and decision-")])
    join_line(b, _line("specific – i.e. in relation"))
    resolve_line_hyphens([b])
    assert b.text == "time-specific and decision-specific – i.e. in relation"


def test_prefix_hyphen_kept():
    b = Block("paragraph", [Run("steps to re-")])
    join_line(b, _line("possess goods"))
    assert b.text == "steps to re-possess goods"


def test_capitalised_next_line_keeps_hyphen_and_space():
    b = Block("paragraph", [Run("Eurosail-")])
    join_line(b, _line("UK plc"))
    assert b.text == "Eurosail- UK plc"


def test_scanned_pdf_rejected(tmp_path):
    # A page that only contains an image, no text layer
    img_doc = fitz.open()
    page = img_doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50), False)
    pix.clear_with(200)
    page.insert_image(fitz.Rect(50, 50, 300, 300), pixmap=pix)
    path = tmp_path / "scan.pdf"
    img_doc.save(path)

    with pytest.raises(NoTextLayerError, match="scanned"):
        parse_file(path)


def test_simple_document(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "1. INTRODUCTION", fontname="hebo", fontsize=11)
    page.insert_text((72, 130), "(a) Background", fontname="hebo", fontsize=11)
    page.insert_text((72, 160), "•", fontname="helv", fontsize=11)
    page.insert_text((90, 160), "First point that is", fontname="helv", fontsize=11)
    page.insert_text((90, 173), "wrapped onto a second line", fontname="helv", fontsize=11)
    page.insert_text((90, 200), "•", fontname="helv", fontsize=11)
    page.insert_text((108, 200), "Nested point", fontname="helv", fontsize=11)
    path = tmp_path / "simple.pdf"
    doc.save(path)

    parsed = parse_file(path)
    kinds = [(b.kind, b.style_key, b.level, b.text) for b in parsed.blocks]
    assert kinds == [
        ("heading", "decimal", 0, "1. INTRODUCTION"),
        ("heading", "alpha", 0, "(a) Background"),
        ("bullet", None, 0, "First point that is wrapped onto a second line"),
        ("bullet", None, 1, "Nested point"),
    ]
