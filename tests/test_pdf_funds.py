"""Checks against the "Lecture Funds" slide deck."""

import pytest
from conftest import SAMPLES, parse_sample

FUNDS = SAMPLES / "Lecture Funds.pdf"


@pytest.fixture(scope="module")
def funds(settings):
    return parse_sample(FUNDS, settings)


def cell_text(table, row, col):
    cell = next(c for c in table.cells if (c.row, c.col) == (row, col))
    return [b.text for b in cell.blocks]


def test_boxed_columns_are_a_table(funds):
    # Slide 6: "Mutual Fund" and "Hedge Fund" boxes side by side, each a
    # shaded heading box over a bordered box of bullets. Slide 7 repeats the
    # headings, so its rows join the same table.
    tables = [b for b in funds.blocks if b.kind == "table"]
    assert len(tables) == 1 and tables[0].page == 6
    t = tables[0].table
    assert (t.n_rows, t.n_cols) == (3, 2)
    assert cell_text(t, 0, 0) == ["Mutual Fund"] and cell_text(t, 0, 1) == ["Hedge Fund"]
    assert cell_text(t, 1, 0)[0].startswith("Transparency: Regulations require")
    assert cell_text(t, 1, 1)[0].startswith("Transparency: Limited liability")
    assert cell_text(t, 2, 1)[0].startswith("Investment Strategies: Very flexible")
    assert not any(b.text == "Hedge Fund" for b in funds.blocks if b.kind != "table")


def test_numbered_points_carry_on_onto_the_next_slide(funds):
    # Slide 27 has points 1-3; slide 28 (same title) starts with a bold
    # "4. Changing Factor Loadings", which is point 4, not a sub-heading
    from notes2gdoc.lists import display_labels

    labels = display_labels(funds.blocks)
    by_text = {b.text: b for b in funds.blocks}
    for text, label in (("Survivorship bias:", "3."), ("Changing Factor Loadings", "4."),
                        ("Tail events – unpredictable massive events", "5.")):
        assert labels.get(id(by_text[text])) == label
    examples = by_text["Examples:"]
    crisis = by_text["The 2008 Global Financial crisis"]
    assert crisis.level == examples.level + 1
