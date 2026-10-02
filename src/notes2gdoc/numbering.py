"""Recognising heading numbers such as "1.", "(a)" and "(ii)", and working out
whether an ambiguous "(i)" is the roman numeral one or the letter i.

The tricky part
---------------
Legal outlines nest like this:

    1.  LIQUIDATION              <- "decimal"
    (a) Overview                 <- "alpha"
    (i) Inability to pay debts   <- "roman"
    (ii) ...

Letters like i, v, x, l, c, d and m are valid roman numerals too. So "(i)",
"(v)", "(x)" and even "(c)" can't be classified by a regex alone. We decide by
context, in `classify_paren_markers` below:

1. Track the last letter heading and the last roman heading seen in the current
   section. A decimal heading resets both, and a new letter heading resets the
   roman counter, because romans are children of letters.
2. Work out what the *next* letter would be (after "(h)" comes "i") and what the
   *next* roman would be (after "(iii)" comes "iv"; with no roman yet it's "i").
3. If the marker matches only one of them, that's the answer.
4. If it matches both (the classic case: "(i)" straight after "(h)"):
   a. If the letter and roman headings so far sit at clearly different
      x-positions, pick whichever this heading lines up with.
   b. Otherwise look ahead. If "(ii)" turns up before the next letter or decimal
      heading, this "(i)" starts a roman list. If not, it's the letter i.
5. If it matches neither (out of sequence), fall back to the shape: multi-letter
   roman-looking markers ("iv", "vi") are roman, single letters are letters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "1." / "12." followed by whitespace or end. We don't treat "1.1" as decimal
# level 1; that would need its own key if a future document uses it.
DECIMAL_RE = re.compile(r"^\s*(\d{1,3})\.(?=\s|$)")
# "(a)" "(iv)" "(aa)". Up to 5 letters, so "(viii)" fits.
PAREN_RE = re.compile(r"^\s*\(([a-zA-Z]{1,5})\)")

_ROMAN_RE = re.compile(r"^m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")


def is_roman(token: str) -> bool:
    t = token.lower()
    return bool(t) and bool(_ROMAN_RE.match(t))


def roman_to_int(token: str) -> int:
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    t = token.lower()
    total = 0
    for i, ch in enumerate(t):
        v = values[ch]
        if i + 1 < len(t) and values[t[i + 1]] > v:
            total -= v
        else:
            total += v
    return total


def int_to_roman(n: int) -> str:
    table = [
        (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
        (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
    ]
    out = []
    for value, sym in table:
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


def next_letter(token: str | None) -> str:
    """'a' -> 'b', 'z' -> 'aa', None -> 'a'."""
    if not token:
        return "a"
    t = token.lower()
    if len(set(t)) == 1 and t[0] == "z":
        return "a" * (len(t) + 1)
    if len(set(t)) == 1:  # "aa" -> "bb" style used by some drafters
        return chr(ord(t[0]) + 1) * len(t)
    return t[:-1] + chr(ord(t[-1]) + 1)


@dataclass
class Marker:
    """A numbering marker found at the start of a heading line."""

    scheme: str        # "decimal" or "paren"
    token: str         # "1", "a", "ii" ...
    label: str         # literal text, e.g. "(ii)" or "1."


def match_marker(text: str) -> Marker | None:
    m = DECIMAL_RE.match(text)
    if m:
        return Marker("decimal", m.group(1), m.group(0).strip())
    m = PAREN_RE.match(text)
    if m:
        return Marker("paren", m.group(1).lower(), m.group(0).strip())
    return None


@dataclass
class HeadingCandidate:
    """Input to the classifier: one numbered heading, in document order."""

    marker: Marker
    x: float = 0.0          # x-position of the marker
    # Filled in by classify_paren_markers: "decimal", "alpha" or "roman"
    style_key: str = ""
    reason: str = ""


def classify_paren_markers(cands: list[HeadingCandidate]) -> None:
    """Set .style_key and .reason on each candidate (in place). See module docstring."""
    last_letter: str | None = None
    last_roman: int | None = None
    letter_xs: list[float] = []
    roman_xs: list[float] = []

    for idx, cand in enumerate(cands):
        mk = cand.marker
        if mk.scheme == "decimal":
            cand.style_key = "decimal"
            cand.reason = f"decimal number {mk.label}"
            last_letter, last_roman = None, None
            continue

        tok = mk.token
        exp_letter = next_letter(last_letter)
        exp_roman = int_to_roman((last_roman or 0) + 1)
        can_letter = tok == exp_letter
        can_roman = is_roman(tok) and tok == exp_roman

        if can_letter and can_roman:
            kind, why = _resolve_ambiguous(cands, idx, cand.x, letter_xs, roman_xs, last_letter)
        elif can_letter:
            kind, why = "alpha", f"next letter after ({last_letter})" if last_letter else "first letter"
        elif can_roman:
            kind, why = "roman", (
                f"next roman after ({int_to_roman(last_roman)})" if last_roman else "first roman"
            ) + (f" under ({last_letter})" if last_letter else "")
        else:
            # Out of sequence: guess from the token's shape.
            if is_roman(tok) and (len(tok) > 1 or tok in ("i", "v", "x")) and last_letter:
                kind, why = "roman", f"out of sequence; ({tok}) looks roman"
            else:
                kind, why = "alpha", f"out of sequence; ({tok}) treated as a letter"

        cand.style_key, cand.reason = kind, why
        if kind == "alpha":
            last_letter = tok
            last_roman = None
            letter_xs.append(cand.x)
        else:
            last_roman = roman_to_int(tok)
            roman_xs.append(cand.x)


def _resolve_ambiguous(cands, idx, x, letter_xs, roman_xs, last_letter):
    tok = cands[idx].marker.token

    # (a) Indentation, when the document indents romans differently from letters.
    if letter_xs and roman_xs:
        lx = sum(letter_xs) / len(letter_xs)
        rx = sum(roman_xs) / len(roman_xs)
        if abs(lx - rx) > 4:
            if abs(x - rx) < abs(x - lx):
                return "roman", f"({tok}) ambiguous; indented like earlier roman headings"
            return "alpha", f"({tok}) ambiguous; indented like earlier letter headings"

    # (b) Look ahead for the roman that would follow, e.g. "(ii)" after "(i)".
    following_roman = int_to_roman(roman_to_int(tok) + 1)
    following_letter = next_letter(tok)
    for later in cands[idx + 1:]:
        mk = later.marker
        if mk.scheme == "decimal":
            break
        if mk.token == following_roman:
            return "roman", f"({tok}) after ({last_letter}) is roman: ({following_roman}) follows"
        if mk.token == following_letter or (not is_roman(mk.token)):
            break
    return "alpha", f"({tok}) after ({last_letter}) is a letter: no ({following_roman}) follows"
