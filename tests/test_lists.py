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


def convert(*blocks, pdf=False):
    doc = Document(list(blocks))
    convert_numbered_lists(doc, hang=pdf)   # pdf: lists may carry on past headings and text
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


def test_bullet_between_items_stays_a_bullet_inside_the_list():
    note = Block("bullet", [Run("The Court of Appeal ruled ...")], level=0)
    b = convert(P("1. To avoid doubt"), note, P("2. Take detailed attendance notes"))
    assert b[1].in_list and b[1].kind == "bullet" and b[1].level == 1
    assert list(display_labels(b).values()) == ["1.", "2."]
    # In the doc: the two items share one numbered list, so "2." follows "1.";
    # the bullet between them is a bulleted list of its own, indented one
    # level in (as its own list it's that list's top level, as in the real Docs)
    doc, _ = append(b)
    ps = paras(doc)[3:]
    assert [(p[1], p[2]) for p in ps] == [
        (0, "To avoid doubt"), (0, "The Court of Appeal ruled ..."), (0, "Take detailed attendance notes")]
    lists = [p["paragraph"].get("bullet", {}).get("listId") for p in _body(doc)[-3:]]
    assert lists[0] == lists[2] and lists[1] not in (None, lists[0])
    assert _body(doc)[-2]["paragraph"]["paragraphStyle"]["indentStart"]["magnitude"] == 72


def test_list_carries_on_past_headings_and_text():
    heading = Block("heading", [Run("B. Sources of Civil Procedural Law")], style_key="alpha")
    b = convert(P("1. Court of Appeal"), P("(a) Original jurisdiction"), P("continuing text of (a)"),
                P("2. District Court"), heading, P("3. Statutes"), P("ii. a stray label"), P("4. Case law"),
                pdf=True)
    numbered = [x.text for x in b if x.numbered]
    assert numbered == ["Court of Appeal", "Original jurisdiction", "District Court", "Statutes", "Case law"]
    assert list(display_labels(b).values()) == ["1.", "a.", "2.", "3.", "4."]
    assert heading.in_list and heading.kind == "heading"
    assert b[2].in_list and b[2].level == 2                        # text under item (a)
    assert b[6].text == "ii. a stray label" and b[6].in_list       # typed, inside the list
    # ...but a sub-level can't start after a heading ("a." on the next slide isn't under "3.")
    h2 = Block("heading", [Run("Next slide")], style_key="slide_title")
    c = convert(P("1. One"), P("2. Two"), h2, P("a. A male party"), P("b. A female party"), pdf=True)
    assert [x.numbered is not None for x in c] == [True, True, False, False, False] and not h2.in_list
    doc, _ = append(b)
    body = [c["paragraph"] for c in _body(doc)[-8:]]
    ids = [p.get("bullet", {}).get("listId") for p in body]
    assert ids[0] is not None and ids[0] == ids[1] == ids[3] == ids[5] == ids[7]   # one list throughout
    assert ids[2] is None and ids[4] is None and ids[6] is None
    assert body[4]["paragraphStyle"]["namedStyleType"] == "HEADING_2"


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
    assert duties.n_cols == 2 and "Keep accounts" in duties.cell(1, 1).text
    assert duties.cell(0, 0).text == "Code of Practice, Section 8.5"   # the bold line above it
    disq = table_with("Family member of donor")
    assert disq.cell(0, 0).text.startswith("Persons disqualified") and "NOTE" in disq.cell(1, 1).text
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
    assert pg.n_cols == 2 and "place to furnish such information" in pg.cell(1, 1).text
    assert pg.cell(0, 0).text == "Sections 31 & 32, MCA: -"
    # Slide 30's narrow label table doesn't swallow slide 31's two equal columns
    assert next(b for b in doc.blocks if b.kind == "table" and "Section 13(1)" in b.text).table.n_rows == 1


def test_mca_deck_round_four():
    doc = parse_sample(MCA)
    # Slides 26 and 27 (same columns, no title row) are one table
    t = next(b for b in doc.blocks if b.kind == "table" and "Section 12(3)" in b.text).table
    assert t.n_rows == 2 and t.cell(1, 0).text.startswith("Section 12(4)")
    # Typed numbers that can't be a real list still hang like one
    office = find(doc, "1. Office of the Public Guardian")
    assert office.hanging and office.level == 0
    vol = next(b for b in doc.blocks if b.kind == "table" and "Voluntary" in b.text).table
    levels = [(b.text[:2], b.level) for b in vol.cell(1, 0).blocks if b.hanging]
    assert levels == [("1.", 0), ("2.", 0), ("a.", 1), ("b.", 1), ("3.", 0)]
    # ...with bullets between items nested under them; row labels left alone
    jointly = t.cell(1, 1).blocks
    assert [b.level for b in jointly if b.kind == "bullet"] == [1, 1, 1, 1]
    bkr = next(b for b in doc.blocks if b.kind == "table" and "Functional Component" in b.text).table
    assert not bkr.cell(0, 0).blocks[0].hanging


SAPT = SAMPLES / "2.%20Single%20Application%20Pending%20Trial.pdf"


def test_sapt_deck():
    """Body-sized bold lines, tight paragraphs, boxes drawn as pictures."""
    doc = parse_sample(SAPT)
    assert not any(b.style_key == "slide_subtitle" and "Permission is not required" in b.text for b in doc.blocks)
    # Paragraphs split on the slightly bigger gap between them; "defence." stays with its sentence
    p = find(doc, "Permission is not required to file the application if")
    assert p.kind == "bullet" and "entire action or defence. See Order 9 Rule 9(7)(h)." in p.text
    assert find(doc, "Amendment by written agreement").text == "Amendment by written agreement. See Order 9 Rule 14(5)."
    # "- Note that …" is a sub-point; the slide's own bullets nest under their paragraph
    note = find(doc, "Note that such an amendment")
    assert (note.kind, note.level) == ("bullet", 1) and not note.text.startswith("-")
    assert find(doc, "In general: see Sheagar").level == 1
    # An all-bold line leads the paragraphs after it
    assert find(doc, "Objectives of third party procedures").level == 0
    assert find(doc, "“Regardless of which limb").level == 1
    # Three picture boxes side by side: a one-row table
    t = next(b for b in doc.blocks if b.kind == "table" and "abuse of process" in b.text).table
    assert (t.n_rows, t.n_cols) == (1, 3) and "Order 9 Rule 16(1)(b)" in t.cell(0, 1).text
    # Lines in one box of a graphic are one point
    assert find(doc, "All known adverse documents").text == "All known adverse documents Order 11, Rule 2(1)(b)"


CIVLIT = SAMPLES / "Civil_Litigation_Detailed_Syllabus_(B26S2).pdf"


def test_outline_document_is_one_numbered_list():
    """A legal outline: "I." / "A." section headings and one numbered list
    (1. -> (a) -> i.) that keeps counting through the whole document."""
    doc = parse_sample(CIVLIT)
    assert find(doc, "I. INTRODUCTION").kind == "heading"
    assert find(doc, "B. Sources of Civil Procedural Law").style_key == "alpha"
    # an all-bold item in the middle of the list is still an item, not a heading
    third = find(doc, "General Division of the High Court")
    assert third.numbered and third.level == 0 and third.runs[0].bold
    labels = display_labels(doc.blocks)
    assert labels[id(third)] == "3." and labels[id(find(doc, "Statutes (SCJA and SCA)"))] == "8."
    assert labels[id(find(doc, "Original Jurisdiction and Powers"))] == "a."
    tops = [b for b in doc.blocks if b.numbered and b.level == 0]
    assert labels[id(tops[-1])] == f"{len(tops)}." and len(tops) > 150      # no restarts, no gaps
    # a stray label between items stays as typed text inside the list
    assert find(doc, "ii. Originating Application").in_list
    # the wrapped end of a bold item is part of that item
    assert find(doc, "Conversion of originating application").text.endswith("Order 15 Rule 7(6)(c) of the ROC 2021.")
    # written in one piece, so the numbering can't start again half-way
    from notes2gdoc.gdocs import writer
    texts = [seg for kind, seg in writer.segments([b for b in doc.blocks if b.selected]) if kind == "text"]
    assert sum(1 for seg in texts if any(b.numbered for b in seg)) == 1


def test_slide_with_lettered_list_numbers_every_main_point():
    # No bullets on the slide, only (a)/(b) typed: the paragraphs are 1., 2.
    blocks = [P("Section 12(1), MCA:"), P("(a) where the power relates only to property –"), P("(b) in any other case –"),
              P("Section 12(2), MCA:"), P("A person who is an undischarged bankrupt may not be appointed.")]
    out = convert(*bulletise_slide(blocks, lambda b: 0), pdf=True)
    assert [(b.level, b.text) for b in out] == [
        (0, "Section 12(1), MCA:"), (1, "where the power relates only to property –"), (1, "in any other case –"),
        (0, "Section 12(2), MCA: A person who is an undischarged bankrupt may not be appointed.")]
    assert list(display_labels(out).values()) == ["1.", "a.", "b.", "2."]


def test_slide_with_bullets_and_letters_gets_bulleted_paragraphs():
    own = Block("bullet", [Run("Yeo Pei Chern v Isa Seow")])
    blocks = [P("“Location does not determine the validity”"), own, P("Essential elements :"),
              P("a. A male party"), P("b. A female party"), P("In the Affidavit, highlight these elements.")]
    out = convert(*bulletise_slide(blocks, lambda b: 0, nest_bullets=True), pdf=True)
    assert [(b.kind, b.level) for b in out] == [
        ("bullet", 0), ("bullet", 1), ("bullet", 0), ("paragraph", 1), ("paragraph", 1), ("bullet", 0)]
    assert out[3].hanging and out[3].text == "a. A male party"       # typed letters under their paragraph
