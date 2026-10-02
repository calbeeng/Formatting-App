"""Symbol and Wingdings font characters -> real Unicode.

Office files often type σ, ≥ or → using the "Symbol" or "Wingdings" font,
storing a private-use character that only looks right in that font. In a
Google Doc those would vanish, so they're translated here.
"""

from __future__ import annotations

# Symbol font: Greek letters and maths signs (by the character's low byte)
_SYMBOL = {
    **dict(zip("abgdezhqiklmnxoprstufcywjv", "αβγδεζηθικλμνξοπρστυφχψωϕϖ")),
    **dict(zip("ABGDEZHQIKLMNXOPRSTUFCYW", "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ")),
    0xA3: "≤", 0xB3: "≥", 0xB9: "≠", 0xBB: "≈", 0xB1: "±", 0xB4: "×", 0xB8: "÷", 0xA5: "∞",
    0xAE: "→", 0xAC: "←", 0xAD: "↑", 0xAF: "↓", 0xAB: "↔", 0xDE: "⇒", 0xDC: "⇐", 0xDB: "⇔",
    0xD6: "√", 0xE5: "∑", 0xB6: "∂", 0xB7: "•", 0xBC: "…", 0xD7: "·", 0xA2: "′", 0xB2: "″",
    0xCE: "∈", 0xC7: "∩", 0xC8: "∪", 0xD9: "∧", 0xDA: "∨", 0xD8: "¬", 0xB0: "°", 0x2D: "−",
}
# Wingdings: the arrows, ticks and bullets people actually type
_WINGDINGS = {
    0xE0: "→", 0xDF: "←", 0xE1: "↑", 0xE2: "↓", 0xE8: "➔", 0xF0: "⇨", 0xEF: "⇦", 0xD8: "➢",
    0xFC: "✔", 0xFB: "✘", 0xFE: "☑", 0xA7: "▪", 0x6C: "●", 0x6E: "■", 0x71: "❑", 0x76: "❖",
    0x77: "◆", 0xA8: "◻", 0x4A: "☺", 0x4C: "☹", 0x46: "☞",
}


def _lookup(table: dict, low: int) -> str | None:
    return table.get(low) or table.get(chr(low))


def is_symbol_font(font: str | None) -> bool:
    """The old picture fonts (not Unicode fonts like "Segoe UI Symbol")."""
    name = (font or "").strip().lower()
    return name == "symbol" or name.startswith(("wingdings", "webdings"))


def fix_symbols(text: str, font: str | None) -> str:
    """Translate characters typed in the Symbol/Wingdings fonts.

    Two cases: private-use characters (U+F000-U+F0FF), which can appear in any
    run; and ordinary letters in a run whose font IS Symbol/Wingdings (e.g. a
    "b" in the Symbol font displays as β). Characters with no translation are
    kept (spaces, digits) or, for private-use ones, dropped.
    """
    name = (font or "").lower()
    whole_run = is_symbol_font(font)
    if not whole_run and not any(0xF000 <= ord(c) <= 0xF0FF for c in text):
        return text
    tables = [_WINGDINGS] if "wingding" in name else [_SYMBOL] if "symbol" in name else [_SYMBOL, _WINGDINGS]
    out = []
    for c in text:
        code = ord(c)
        if 0xF000 <= code <= 0xF0FF:
            low = code - 0xF000
            out.append(next((v for t in tables if (v := _lookup(t, low))), ""))
        elif whole_run and code < 0x100 and not c.isspace() and not c.isdigit():
            out.append(next((v for t in tables if (v := _lookup(t, code))), c))
        else:
            out.append(c)
    return "".join(out)
