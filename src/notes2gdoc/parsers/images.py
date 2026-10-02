"""Turning embedded pictures (PNG, JPEG, GIF, BMP, TIFF…) into PNG bytes,
which is what the rest of the app works with."""

from __future__ import annotations

import pymupdf as fitz


def to_png(blob: bytes) -> bytes | None:
    """PNG bytes for an image, or None if the format can't be read (e.g.
    Windows EMF pictures). WMF pictures (equation previews) are drawn by our
    own small renderer."""
    from .wmf import is_wmf, wmf_to_png

    if is_wmf(blob):
        return wmf_to_png(blob)
    try:
        pix = fitz.Pixmap(blob)
    except Exception:
        return None
    if pix.n - pix.alpha > 3:  # CMYK -> RGB
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return pix.tobytes("png")
