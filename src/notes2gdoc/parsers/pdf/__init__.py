"""PDF parser: PyMuPDF spans -> visual lines -> furniture removed -> Blocks."""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz

from ...config import Settings
from ...model import Document
from ..base import NoTextLayerError, ParseError
from .extract import Line, extract_page
from .furniture import remove_furniture
from .layout import is_slide_deck
from .slides import build_slide_blocks
from .structure import build_document_blocks

# PyMuPDF's table finder prints an advert for an add-on package to stdout. In
# the double-click app there's no console, so switch it off.
if hasattr(fitz, "no_recommend_layout"):
    fitz.no_recommend_layout()

# Fewer visible characters than this per page (on average) means "no text layer".
MIN_CHARS_PER_PAGE = 15


class PdfParser:
    extensions = (".pdf",)

    def parse(self, path: Path, settings: Settings) -> Document:
        try:
            pdf = fitz.open(path)
        except Exception as exc:  # PyMuPDF raises various types
            raise ParseError(f"Couldn't open this PDF: {exc}") from exc
        with pdf:
            if pdf.needs_pass:
                raise ParseError("This PDF is password-protected. Remove the password and try again.")
            pages = list(pdf)
            warnings = _check_text_layer(pages)
            lines_by_page = [extract_page(p, i) for i, p in enumerate(pages)]
            body_size = _body_size(lines_by_page)
            slides = is_slide_deck(pages, body_size)
            cleaned, _removed = remove_furniture(lines_by_page, body_size, slides)
            if slides:
                blocks = build_slide_blocks(cleaned, settings)
            else:
                blocks = build_document_blocks(cleaned)
            return Document(
                blocks=blocks,
                source_path=str(path),
                layout="slides" if slides else "document",
                page_count=len(pages),
                warnings=warnings,
            )


def _check_text_layer(pages: list[fitz.Page]) -> list[str]:
    """Raise if the whole PDF is scanned images; warn about individual
    image-only pages."""
    counts = []
    image_only = []
    for i, p in enumerate(pages):
        n = len("".join(p.get_text("text").split()))
        counts.append(n)
        if n == 0 and p.get_images():
            image_only.append(i + 1)
    if not pages or sum(counts) < MIN_CHARS_PER_PAGE * len(pages):
        raise NoTextLayerError(
            "This PDF appears to be scanned (it has no selectable text). "
            "OCR isn't supported yet, so nothing can be extracted. "
            "Tip: run it through an OCR tool (e.g. Adobe Acrobat's 'Scan & OCR') first."
        )
    if image_only:
        return [f"Page(s) {', '.join(map(str, image_only))} look scanned (no text) and were skipped."]
    return []


def _body_size(pages: list[list[Line]]) -> float:
    """Most common font size, weighted by characters."""
    weights: dict[float, int] = {}
    for lines in pages:
        for ln in lines:
            if not isinstance(ln, Line):
                continue
            n = len(ln.text.strip())
            if n:
                weights[round(ln.size)] = weights.get(round(ln.size), 0) + n
    return max(weights, key=weights.get) if weights else 11.0
