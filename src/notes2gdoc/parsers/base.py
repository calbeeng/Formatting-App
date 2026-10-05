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
    parser = get_parser(path)
    readable = _readable_copy(path)
    try:
        doc = parser.parse(readable, settings)
    finally:
        if readable != path:
            readable.unlink(missing_ok=True)
    doc.source_path = str(path)
    from ..lists import convert_numbered_lists
    from ..tables import merge_split_tables

    merge_split_tables(doc)      # a table continued over several slides -> one table
    # typed "1." / "a." lists -> real numbered lists (in slides and PDFs, any
    # that can't be real lists are still laid out like one)
    convert_numbered_lists(doc, hang=doc.layout == "slides" or path.suffix.lower() == ".pdf")
    return doc


def _readable_copy(path: Path) -> Path:
    """`path` itself, or a temporary copy if another program has it locked.

    On Windows, Word and PowerPoint lock the files they have open so other
    programs can't read them directly, but Windows' own copy function still
    can. So we read from a quick temporary copy instead of asking you to
    close the file.
    """
    try:
        with open(path, "rb"):
            return path
    except PermissionError:
        pass
    import os
    import sys
    import tempfile

    if sys.platform == "win32":
        import ctypes

        fd, tmp = tempfile.mkstemp(suffix=path.suffix)
        os.close(fd)
        if ctypes.windll.kernel32.CopyFileW(str(path.resolve()), tmp, False):
            return Path(tmp)
        Path(tmp).unlink(missing_ok=True)
    raise ParseError(
        f"“{path.name}” is open in another program (such as Word or PowerPoint) that's blocking it. "
        "Close it there and try again."
    )
