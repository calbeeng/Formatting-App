"""Entry point for the desktop app (double-click launcher / packaged build).

    "Notes to Google Docs"               open the app
    "Notes to Google Docs" some.pdf      open the app with that file loaded
    "Notes to Google Docs" --self-test   check the packaged app works, then quit
                                         (used by the automatic GitHub builds)
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    from PySide6.QtGui import QIcon

    app = QApplication(sys.argv)
    app.setApplicationName("Notes to Google Docs")
    icon = Path(__file__).resolve().parent.parent / "resources" / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    window.show()
    # Allow "open with": a file path passed on the command line is loaded straight away
    if len(sys.argv) > 1 and Path(sys.argv[1]).is_file():
        window.load(Path(sys.argv[1]))
    return app.exec()


def self_test() -> int:
    """Exercise the pieces most likely to break in a packaged build: Qt and
    its plugins, PyMuPDF, the parser, the preview, and the Google libraries'
    bundled data. Writes results to a log file (a windowed app has no console)
    and returns 0 on success."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    log_path = Path(tempfile.gettempdir()) / "notes2gdoc-self-test.log"
    lines: list[str] = []

    def check(name, fn):
        try:
            fn()
            lines.append(f"ok    {name}")
            return True
        except Exception as exc:  # report everything, then fail
            lines.append(f"FAIL  {name}: {type(exc).__name__}: {exc}")
            return False

    results = []
    state: dict = {}

    def qt_window():
        from PySide6.QtWidgets import QApplication

        from .main_window import MainWindow

        state["app"] = QApplication.instance() or QApplication([])
        state["window"] = MainWindow()

    def parse_pdf():
        import pymupdf

        from ..config import Settings
        from ..parsers import parse_file

        path = Path(tempfile.gettempdir()) / "notes2gdoc-self-test.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 100), "1. INTRODUCTION", fontname="hebo", fontsize=11)
        page.insert_text((72, 130), "•", fontname="helv", fontsize=11)
        page.insert_text((90, 130), "A bullet point", fontname="helv", fontsize=11)
        doc.save(path)
        parsed = parse_file(path, Settings())
        kinds = [b.kind for b in parsed.blocks]
        assert kinds == ["heading", "bullet"], kinds
        state["doc"] = parsed

    def preview():
        from ..config import Settings
        from .preview_html import document_html

        html, _ = document_html(state["doc"], Settings())
        assert "INTRODUCTION" in html

    def google_libraries():
        import googleapiclient.discovery  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
        from googleapiclient import discovery_cache

        docs_json = Path(discovery_cache.__file__).parent / "documents" / "docs.v1.json"
        drive_json = Path(discovery_cache.__file__).parent / "documents" / "drive.v3.json"
        assert docs_json.exists() and drive_json.exists(), "Google API descriptions not bundled"

    results.append(check("Qt window", qt_window))
    results.append(check("read a PDF", parse_pdf))
    results.append(check("preview", preview) if "doc" in state else False)
    results.append(check("Google libraries", google_libraries))

    text = "\n".join(lines) + "\n"
    log_path.write_text(text, encoding="utf-8")
    try:
        sys.stdout.write(text)
    except Exception:
        pass  # no console in a windowed build
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
