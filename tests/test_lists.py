"""Typed list numbers -> real numbered lists, slide text -> points, and tables
continued across slides. Synthetic examples (always run) plus checks against
the Mental Capacity Act deck (skipped when it isn't present)."""

from conftest import SAMPLES, find, parse_sample
from test_writer import append, paras

from notes2gdoc.lists import bulletise_slide, convert_numbered_lists, display_labels
from notes2gdoc.model import Block, Document, Run, Table, TableCell
from notes2gdoc.tables import merge_split_tables

MCA = SAMPLES / "6.%20Mental%20Capacity%20Act.pdf"


def P(text, level=0):
    return Block("paragraph", [Run(text)], level=level)


def convert(*blocks):
    doc = Document(list(blocks))
    convert_numbered_lists(doc)
    return doc.blocks


def test_numbered_list_with_letters_nested():
    b = convert(P("1. Introduction"), P("a. The Donor"), P("b. The Donees"), P("2. Procedure"))
    assert [(x.kind, x.level, x.text) for x in b] == [
        ("bullet", 0, "Introduction"), ("bullet", 1, "The Donor"),
        ("bullet", 1, "The Donees"), ("bullet", 0, "Procedure")]
    assert all(x.numbered == "NUMBERED_DECIMAL_ALPHA_ROMAN" for x in b)
    assert list(display_labels(b).values()) == ["1.", "a.", "b.", "2."]


def test_lone_or_badly_numbered_items_stay_literal():
    b = convert(P("(1) Purpose: companies need working capital"), P("Next paragraph"))
    assert b[0].kind == "paragraph" and b[0].text.startswith("(1) Purpose")
    b = convert(P("(3) third"), P("(4) fourth"))  # an excerpt starting at (3)
    assert [x.text for x in b] == ["(3) third", "(4) fourth"] and not b[0].numbered


def test_restarted_numbering_is_a_new_list():
    b = convert(P("1. a"), P("2. b"), P("1. c"), P("2. d"))
    assert all(x.numbered for x in b)
    assert [x.list_start for x in b] == [False, False, True, False]
    assert list(display_labels(b).values()) == ["1.", "2.", "1.", "2."]


def test_note_between_items_stays_inside_the_list():
    note = Block("bullet", [Run("The Court of Appeal ruled ...")], level=0)
    b = convert(P("1. To avoid doubt"), note, P("2. Take detailed attendance notes"))
    assert b[1].in_list and b[1].kind == "paragraph" and b[1].level == 1
    assert list(display_labels(b).values()) == ["1.", "2."]
    # In the doc: one list, the note un-numbered and indented under item 1
    doc, _ = append(b)
    ps = paras(doc)[3:]
    assert [(p[1], p[2]) for p in ps] == [
        (0, "To avoid doubt"), (None, "The Court of Appeal ruled ..."), (0, "Take detailed attendance notes")]
    lists = [p["paragraph"].get("bullet", {}).get("listId") for p in _body(doc)[-3:]]
    assert lists[0] == lists[2] and lists[0] is not None


def _body(doc):
    return [el for el in doc.to_json()["body"]["content"] if "paragraph" in el]


def test_slide_paragraphs_become_points():
    x = {}

    def at(block, left):
        x[id(block)] = left
        return block

    blocks = [
        at(P("NOTE: Section 6(11) MCA:"), 60),
        at(P("In the case of an act done, or a decision made, by a person other than the court, ..."), 90),
        at(P("Please also see Chapter 6 of the Code of Practice"), 60),
    ]
    out = bulletise_slide(blocks, lambda b: x.get(id(b), 0))
    assert [(b.kind, b.text[:30]) for b in out] == [
        ("bullet", "NOTE: Section 6(11) MCA: In th"), ("bullet", "Please also see Chapter 6 of t")]


def test_list_intro_numbered_and_trailing_text_joined():
    blocks = [P("An LPA is a power of attorney ... any of the following: -"),
              P("(a) P’s personal welfare;"), P("(b) P’s property and affairs,"),
              P("when P no longer has capacity to make such decisions.")]
    out = convert(*bulletise_slide(blocks, lambda b: 0))
    assert [(b.level, b.text) for b in out] == [
        (0, "An LPA is a power of attorney ... any of the following: -"),
        (1, "P’s personal welfare;"),
        (1, "P’s property and affairs, when P no longer has capacity to make such decisions.")]
    assert all(b.numbered for b in out)


def test_slides_with_their_own_bullets_are_left_alone():
    blocks = [P("Intro line"), Block("bullet", [Run("A point")])]
    assert [b.kind for b in bulletise_slide(blocks, lambda b: 0)] == ["paragraph", "bullet"]


def _table(header, *rows):
    t = Table(1 + len(rows), 2, [TableCell(0, 0, [P(header)], colspan=2)])
    for i, (a, b) in enumerate(rows, 1):
        t.cells += [TableCell(i, 0, [P(a)]), TableCell(i, 1, [P(b)])]
    return Block("table", table=t)


def test_table_continued_on_next_slide_is_merged():
    doc = Document([_table("Considerations", ("6(3)", "x")), _table("Considerations", ("6(5)", "y"), ("6(6)", "z")),
                    _table("Something else", ("a", "b"))])
    merge_split_tables(doc)
    assert len(doc.blocks) == 2
    t = doc.blocks[0].table
    assert t.n_rows == 4 and [t.cell(r, 0).text for r in range(1, 4)] == ["6(3)", "6(5)", "6(6)"]


# --------------------------------------------------------------------------- #
# Mental Capacity Act deck (local only)
# --------------------------------------------------------------------------- #

def test_mca_deck():
    doc = parse_sample(MCA)
    # Slide 16: no bullets on the slide, so each paragraph is a point
    note = find(doc, "NOTE: Section 6(11) MCA:")
    assert note.kind == "bullet" and "best interests of the person concerned." in note.text
    # Slide 19: "1." intro, (a)/(b) nested, trailing words joined to (b)
    intro = find(doc, "An LPA is a power of attorney")
    assert intro.numbered and intro.level == 0
    b = find(doc, "P’s property and affairs or specified")
    assert b.level == 1 and b.text.endswith("when P no longer has capacity to make such decisions.")
    # Slides 12-15: one table under the repeated "Considerations" header
    t = next(x for x in doc.blocks if x.kind == "table" and "Section 6(9)" in x.text).table
    assert t.cell(0, 0).colspan == 2 and t.cell(0, 0).text.startswith("Considerations in Sections 6(3)")
    assert [t.cell(r, 0).text.split(" ")[1] for r in range(1, t.n_rows)] == [
        "6(3)", "6(4)", "6(5)", "6(6)", "6(7)", "6(8)", "6(9)"]
    assert t.cell(5, 0).text == "Section 6(7) Disposition of property"
    # Slide 5: a bold label and its text on one line are split into two cells
    s5 = next(x for x in doc.blocks if x.kind == "table" and "Section 3(2)" in x.text).table
    assert (s5.cell(0, 0).text, s5.cell(0, 1).text) == ("Section 3(2)", "Assume person has capacity")
    # A hyphen at a line end is kept when it's part of the word
    assert "decision-specific" in find(doc, "The test for the lack of mental capacity").text


def test_mca_deck_round_two():
    doc = parse_sample(MCA)

    def table_with(text):
        return next(b for b in doc.blocks if b.kind == "table" and text in b.text).table

    # Tables between just two rules, a label column on the left
    t = table_with("Section 13(1)")
    assert t.cell(0, 0).text == "Section 13(1)" and t.cell(0, 1).text.startswith("A donee must not")
    assert table_with("Section 20(1) & 20(2)").cell(0, 0).text == "Section 20(1) & 20(2)"
    assert table_with("Section 6(1)").n_cols == 2                      # slide 11
    # Two columns of bullets side by side (slides 33, 35, 68)
    duties = table_with("Follow the statutory principles")
    assert duties.n_cols == 2 and "Keep accounts" in duties.cell(0, 1).text
    assert "NOTE" in table_with("Family member of donor").cell(0, 1).text
    powers = table_with("where P is to live")
    assert (powers.cell(0, 0).text, powers.cell(0, 1).text) == ("Personal Welfare", "Property & Affairs")
    assert "conduct of legal proceedings" in powers.cell(1, 1).text
    # A centred heading over a block of text: one-column table
    vol = table_with("Voluntary")
    assert vol.n_cols == 1 and vol.cell(1, 0).blocks[0].text == "Section 15(2), MCA"
    # Pictures on slides 32 and 50, but not the logo on every slide
    pics = [b.page for b in doc.blocks if b.kind == "image"]
    assert 32 in pics and pics.count(50) == 2 and 2 not in pics
    # No slide numbers glued onto text, no whole lines marked superscript
    assert not find(doc, "These excluded decisions").text.endswith("32")
    cell = table_with("It does not matter").cell(0, 1).blocks[0]
    assert not any(r.superscript for r in cell.runs)


def test_mca_deck_round_three():
    doc = parse_sample(MCA)
    # Process banner: only the current step, as a heading, once per run of slides
    steps = [b.text for b in doc.blocks if b.style_key == "slide_step"]
    assert "4. Consents / Service on Relevant Persons" in steps
    assert steps.count("1. Originating Application") == 1
    assert not any("Supporting Affidavit" in b.text and "Originating" in b.text for b in doc.blocks)
    # Slides with typed numbers keep their other lines plain
    assert find(doc, "Who are “relevant persons”?").kind == "paragraph"
    # Deputies introduction: the "Section 21" row has no rule under it
    t = next(b for b in doc.blocks if b.kind == "table" and "Section 21" in b.text).table
    assert t.cell(1, 0).text == "Section 21" and "Part 5 Rule 8(4)" in t.cell(1, 1).text
    # Public Guardian functions: two columns of a numbered list
    pg = next(b for b in doc.blocks if b.kind == "table" and "Establishing & maintaining" in b.text).table
    assert pg.n_cols == 2 and "place to furnish such information" in pg.cell(0, 1).text
