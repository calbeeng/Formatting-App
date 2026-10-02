"""Common parser interface and lookup by file extension."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..config import Settings
from ..model import Document


class ParseError(Exception):
    """A problem the user should see as a plain message (not a crash)."""


class NoTextLayerError(ParseError):
    """The file is a scanned image with no selectable text."""


class Parser(Protocol):
    extensions: tuple[str, ...]

    def parse(self, path: Path, settings: Settings) -> Document: ...


def get_parser(path: str | Path) -> Parser:
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        from .pdf import PdfParser

        return PdfParser()
    if suffix == ".docx":
        from .docx_parser import DocxParser

        return DocxParser()
    if suffix == ".pptx":
        from .pptx_parser import PptxParser

        return PptxParser()
    if suffix in (".doc", ".ppt"):
        new = suffix + "x"
        raise ParseError(
            f"This is an old-style {suffix} file. Open it in Word/PowerPoint and use "
            f"File → Save As → {new}, then open the {new} file here."
        )
    raise ParseError(f"Unsupported file type '{suffix}'. Use a PDF, Word (.docx) or PowerPoint (.pptx) file.")


def parse_file(path: str | Path, settings: Settings | None = None) -> Document:
    settings = settings or Settings()
    path = Path(path)
    if not path.exists():
        raise ParseError(f"File not found: {path}")
    return get_parser(path).parse(path, settings)
