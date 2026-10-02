from notes2gdoc.numbering import (
    HeadingCandidate,
    classify_paren_markers,
    int_to_roman,
    is_roman,
    match_marker,
    next_letter,
    roman_to_int,
)


def classify(tokens, xs=None):
    """tokens like ["1.", "(a)", "(i)"]; returns list of style keys."""
    cands = [
        HeadingCandidate(match_marker(t + " Heading"), x=(xs[i] if xs else 72.0))
        for i, t in enumerate(tokens)
    ]
    classify_paren_markers(cands)
    return [c.style_key for c in cands]


def test_roman_helpers():
    assert is_roman("iv") and is_roman("viii") and is_roman("c")
    assert not is_roman("iiii") and not is_roman("b") and not is_roman("")
    assert roman_to_int("xiv") == 14
    assert int_to_roman(9) == "ix"
    assert next_letter(None) == "a" and next_letter("h") == "i" and next_letter("z") == "aa"


def test_match_marker():
    assert match_marker("1. LIQUIDATION").label == "1."
    assert match_marker("(ii) Presumption").token == "ii"
    assert match_marker("1.5 million") is None
    assert match_marker("Section 1") is None


def test_roman_under_letter():
    assert classify(["1.", "(a)", "(b)", "(i)", "(ii)", "(c)"]) == [
        "decimal", "alpha", "alpha", "roman", "roman", "alpha"]


def test_i_after_h_with_no_ii_is_letter():
    # Syllabus: "(h) Pari Passu" then "(i) Dissolution" then "2. BANKRUPTCY"
    keys = classify(["1.", "(a)", "(b)", "(c)", "(d)", "(e)", "(f)", "(g)", "(h)", "(i)", "2."])
    assert keys[9] == "alpha"


def test_i_after_h_followed_by_ii_is_roman():
    keys = classify(["(g)", "(h)", "(i)", "(ii)", "(j)"])
    assert keys == ["alpha", "alpha", "roman", "roman", "alpha"]


def test_i_after_g_is_roman():
    # Syllabus: "(g) Claims" then "(i) Provable Debts" (no (ii)) then "(h)"
    assert classify(["(f)", "(g)", "(i)", "(h)"]) == ["alpha", "alpha", "roman", "alpha"]


def test_v_continues_roman_sequence():
    assert classify(["(a)", "(i)", "(ii)", "(iii)", "(iv)", "(v)", "(b)"]) == [
        "alpha", "roman", "roman", "roman", "roman", "roman", "alpha"]


def test_c_and_d_are_letters():
    assert classify(["(a)", "(b)", "(c)", "(d)"]) == ["alpha"] * 4


def test_ambiguous_resolved_by_indent():
    # Romans indented at 100pt, letters at 72pt: "(i)" at 72 after "(h)" is a letter
    # even though a "(ii)" (belonging to something else) appears later.
    tokens = ["(a)", "(i)", "(b)", "(c)", "(d)", "(e)", "(f)", "(g)", "(h)", "(i)", "(ii)"]
    xs = [72, 100, 72, 72, 72, 72, 72, 72, 72, 72, 100]
    assert classify(tokens, xs)[9] == "alpha"
