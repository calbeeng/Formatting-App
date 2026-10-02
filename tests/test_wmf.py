"""The small WMF (Windows Metafile) renderer used for equation previews."""

import struct

import pymupdf as fitz

from notes2gdoc.parsers.images import to_png
from notes2gdoc.parsers.wmf import is_wmf


def _record(func: int, payload: bytes) -> bytes:
    if len(payload) % 2:
        payload += b"\x00"
    return struct.pack("<IH", 3 + len(payload) // 2, func) + payload


def _font(height: int, face: bytes, italic: bool = False) -> bytes:
    data = struct.pack("<hhhhh", height, 0, 0, 0, 400) + bytes([1 if italic else 0, 0, 0, 0, 0, 0, 0, 0])
    return _record(0x02FB, data + face.ljust(32, b"\x00"))


def _ext_text(x: int, y: int, text: bytes, dx=None) -> bytes:
    data = struct.pack("<hhhH", y, x, len(text), 0) + text + (b"\x00" if len(text) % 2 else b"")
    if dx:
        data += struct.pack(f"<{len(dx)}h", *dx)
    return _record(0x0A32, data)


def make_wmf() -> bytes:
    body = b"".join([
        _record(0x012E, struct.pack("<H", 24 | 1)),               # baseline + update current position
        _record(0x020B, struct.pack("<hh", 0, 0)),                 # window origin
        _record(0x020C, struct.pack("<hh", 600, 2400)),            # window extent (y, x)
        _font(-400, b"Times New Roman", italic=True),
        _record(0x012D, struct.pack("<H", 0)),
        _record(0x0214, struct.pack("<hh", 450, 100)),             # MoveTo (y, x)
        _ext_text(0, 0, b"xy", dx=[600, 600]),
        _font(-400, b"Symbol"),
        _record(0x012D, struct.pack("<H", 1)),
        _record(0x0214, struct.pack("<hh", 450, 1400)),
        _ext_text(0, 0, b"s"),                                     # Symbol "s" = sigma
        _record(0x0213, struct.pack("<hh", 550, 2300)),            # LineTo
        struct.pack("<IH", 3, 0),                                  # end of file
    ])
    header = struct.pack("<HHHIHIH", 1, 9, 0x0300, (18 + len(body)) // 2, 2, 20, 0)
    return header + body


def test_wmf_renders_text_and_lines():
    blob = make_wmf()
    assert is_wmf(blob)
    png = to_png(blob)
    assert png and png.startswith(b"\x89PNG")
    pix = fitz.Pixmap(png)
    assert pix.width > 100 and pix.height > 20
    # Something was actually drawn (not a blank white image)
    samples = pix.samples
    assert min(samples) < 100


def test_non_wmf_not_detected():
    assert not is_wmf(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
