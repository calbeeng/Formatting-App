"""The main window.

    ┌ Google account ─────────────────────────── [Settings…] [Sign in] ┐
    │ [Open file…] name.pdf          [Strip colour] [Show skipped] [Why]│
    │ ┌ tick-box outline ──────┐ ┌ preview (looks like the doc) ──────┐ │
    │ │ [Tick all] [Untick all]│ │                                    │ │
    │ │ Pages 1 to 13          │ │                                    │ │
    │ └────────────────────────┘ └────────────────────────────────────┘ │
    │ Google Doc: [recent docs / paste link ▾] [↻]  ✓ My notes          │
    │ Insert at:  [End of the document ▾]        [Dry run] [Append 57]  │
    └───────────────────────────────────────────────────────────────────┘

Anything slow (reading a file, signing in, loading a doc, appending) runs on
a background thread via `_Task`, so the window never freezes.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import recent
from ..config import Settings
from ..model import Document
from ..parsers import ParseError, parse_file
from .outline_tree import OutlineTree
from .preview_html import document_html
from .settings_dialog import SettingsDialog

SUPPORTED = (".pdf", ".docx", ".pptx")
FILE_FILTER = "Study files (*.pdf *.docx *.pptx);;PDF (*.pdf);;Word (*.docx);;PowerPoint (*.pptx)"
SETUP_HELP = (
    "This copy of the app needs a one-time setup: the Google “client file” you "
    "downloaded when following SETUP.md (it’s usually called client_secret_….json).\n\n"
    "Click OK to choose that file."
)
END_OF_DOC = "At the end of the document"


class _Signals(QObject):
    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    """Runs `fn(progress)` on a background thread. `fn` receives a progress
    callback (done, total, message); its return value goes to `done`."""

    def __init__(self, fn: Callable, error_prefix: str):
        super().__init__()
        self.fn, self.error_prefix = fn, error_prefix
        self.signals = _Signals()

    def run(self):
        try:
            result = self.fn(lambda d, t, m: self.signals.progress.emit(d, t, m))
            self.signals.done.emit(result)
        except Exception as exc:
            self.signals.failed.emit(_describe_error(exc, self.error_prefix))


def _describe_error(exc: Exception, prefix: str) -> str:
    from ..gdocs import AppendError, friendly_http_error
    from ..gdocs.auth import AuthError

    if isinstance(exc, (ParseError, AppendError, AuthError, ValueError)):
        return str(exc)
    friendly = friendly_http_error(exc)
    if friendly:
        return friendly
    return f"{prefix}\n\n{type(exc).__name__}: {exc}\n\n{traceback.format_exc(limit=3)}"


class DropZone(QLabel):
    def __init__(self):
        super().__init__("Drag a PDF, Word or PowerPoint file here\n\nor click “Open file…” above")
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "QLabel { border: 2px dashed #9AA0A6; border-radius: 12px; color: #5F6368;"
            " font-size: 15pt; margin: 24px; }"
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Notes → Google Docs")
        self.resize(1180, 820)
        self.setAcceptDrops(True)
        self.settings = Settings.load()
        self.doc: Document | None = None
        self.doc_path: Path | None = None
        self.doc_info = None          # gdocs.DocInfo of the chosen Google Doc
        self._loading_doc_id = None
        self._tasks: list[_Task] = []
        self._creds = None

        # --- Account row --------------------------------------------------------
        self.account_label = QLabel()
        self.settings_btn = QPushButton("Settings…")
        self.settings_btn.clicked.connect(self.open_settings)
        self.account_btn = QPushButton()
        self.account_btn.clicked.connect(self._account_clicked)
        account = QHBoxLayout()
        account.addWidget(self.account_label, 1)
        account.addWidget(self.settings_btn)
        account.addWidget(self.account_btn)

        # --- File row -------------------------------------------------------------
        self.open_btn = QPushButton("Open file…")
        self.open_btn.clicked.connect(self.choose_file)
        self.file_label = QLabel("No file chosen")
        self.file_label.setStyleSheet("color: #5F6368;")
        self.show_skipped = QCheckBox("Show skipped items")
        self.show_skipped.setChecked(True)
        self.show_skipped.setToolTip("Show unticked items (greyed, marked SKIPPED) in the preview")
        self.show_notes = QCheckBox("Show why")
        self.show_notes.setToolTip("Show the page number and why each line was classified the way it was")
        self.strip_colour = QCheckBox("Strip colour")
        self.strip_colour.setChecked(self.settings.strip_colour)
        for cb in (self.show_skipped, self.show_notes, self.strip_colour):
            cb.toggled.connect(self.refresh)
        top = QHBoxLayout()
        top.addWidget(self.open_btn)
        top.addWidget(self.file_label, 1)
        top.addWidget(self.strip_colour)
        top.addWidget(self.show_skipped)
        top.addWidget(self.show_notes)

        # --- Left: tick-box outline -------------------------------------------------
        self.tree = OutlineTree()
        self.tree.selection_changed.connect(self._selection_changed)
        tick_all = QPushButton("Tick all")
        tick_all.clicked.connect(lambda: self.tree.set_all(True))
        untick_all = QPushButton("Untick all")
        untick_all.clicked.connect(lambda: self.tree.set_all(False))
        self.page_from = QSpinBox()
        self.page_to = QSpinBox()
        for sb in (self.page_from, self.page_to):
            sb.setRange(1, 1)
            sb.valueChanged.connect(self._page_range_changed)
        tick_row = QHBoxLayout()
        tick_row.addWidget(tick_all)
        tick_row.addWidget(untick_all)
        tick_row.addStretch(1)
        tick_row.addWidget(QLabel("Pages"))
        tick_row.addWidget(self.page_from)
        tick_row.addWidget(QLabel("to"))
        tick_row.addWidget(self.page_to)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(tick_row)
        left_layout.addWidget(self.tree, 1)

        # --- Right: preview ---------------------------------------------------------
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.stack = QStackedWidget()
        self.stack.addWidget(DropZone())
        self.stack.addWidget(self.preview)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.stack)
        self.splitter.setSizes([420, 760])
        left.hide()  # shown once a file is open
        self._left_panel = left

        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("background:#FEF7E0; color:#7A5200; padding:6px; border-radius:4px;")
        self.warning.hide()

        # --- Bottom: where to append ------------------------------------------------
        self.doc_combo = QComboBox()
        self.doc_combo.setEditable(True)
        self.doc_combo.setInsertPolicy(QComboBox.NoInsert)
        self.doc_combo.lineEdit().setPlaceholderText("Paste a Google Doc link, or pick a recent doc")
        self.doc_combo.activated.connect(lambda _: self._doc_changed())
        self.doc_combo.lineEdit().editingFinished.connect(self._doc_changed)
        self.reload_btn = QToolButton()
        self.reload_btn.setText("↻")
        self.reload_btn.setToolTip("Reload this doc's headings")
        self.reload_btn.clicked.connect(lambda: self._doc_changed(force=True))
        self.doc_status = QLabel()
        self.insert_combo = QComboBox()
        self.insert_combo.addItem(END_OF_DOC, None)
        self.insert_combo.setToolTip("Pick a heading to add the notes at the end of that heading’s section")
        self.dry_run = QCheckBox("Dry run")
        self.dry_run.setToolTip("Don't change the doc: save the planned changes to a file instead (for debugging)")
        self.append_btn = QPushButton("Append to Google Doc")
        self.append_btn.setStyleSheet("QPushButton { font-weight: bold; padding: 6px 14px; }")
        self.append_btn.clicked.connect(self.append)
        self.progress = QProgressBar()
        self.progress.hide()

        grid = QGridLayout()
        grid.addWidget(QLabel("Google Doc:"), 0, 0)
        grid.addWidget(self.doc_combo, 0, 1)
        grid.addWidget(self.reload_btn, 0, 2)
        grid.addWidget(self.doc_status, 0, 3, 1, 2)
        grid.addWidget(QLabel("Insert at:"), 1, 0)
        grid.addWidget(self.insert_combo, 1, 1, 1, 2)
        grid.addWidget(self.dry_run, 1, 3)
        grid.addWidget(self.append_btn, 1, 4)
        grid.setColumnStretch(1, 1)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #DADCE0;")

        layout = QVBoxLayout()
        layout.addLayout(account)
        layout.addWidget(line)
        layout.addLayout(top)
        layout.addWidget(self.warning)
        layout.addWidget(self.splitter, 1)
        layout.addLayout(grid)
        layout.addWidget(self.progress)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready")

        self._load_recent(select_url=self.settings.last_doc_url)
        self._restore_sign_in()

    # =========================================================================
    # Background tasks
    # =========================================================================
    def _start(self, fn, on_done, on_failed, error_prefix, on_progress=None):
        task = _Task(fn, error_prefix)
        task.signals.done.connect(on_done)
        task.signals.failed.connect(on_failed)
        if on_progress:
            task.signals.progress.connect(on_progress)
        task.signals.done.connect(lambda *_: self._tasks.remove(task) if task in self._tasks else None)
        task.signals.failed.connect(lambda *_: self._tasks.remove(task) if task in self._tasks else None)
        self._tasks.append(task)  # keep a reference while it runs
        QThreadPool.globalInstance().start(task)

    # =========================================================================
    # Google account
    # =========================================================================
    def _restore_sign_in(self):
        from ..gdocs import auth

        self.account_label.setText("Checking Google sign-in…")
        self.account_btn.setEnabled(False)
        self._start(lambda _: auth.load_credentials(), self._set_creds,
                    lambda msg: self._set_creds(None), "Couldn't check sign-in")

    def _set_creds(self, creds):
        from ..gdocs import auth

        self._creds = creds
        self.account_btn.setEnabled(True)
        if creds:
            email = auth.signed_in_email() or "your Google account"
            self.account_label.setText(f"✅  Signed in as <b>{email}</b>")
            self.account_btn.setText("Sign out")
            self._doc_changed(force=True)
        else:
            self.account_label.setText("Not signed in to Google")
            self.account_btn.setText("Sign in with Google")

    def _account_clicked(self):
        from ..gdocs import auth

        if self._creds:
            auth.sign_out()
            self._set_creds(None)
            return
        if not auth.has_client_file() and not self._ask_for_client_file():
            return
        self.account_label.setText("Waiting for you to sign in in your web browser…")
        self.account_btn.setEnabled(False)
        self._start(lambda _: auth.sign_in(), self._signed_in, self._sign_in_failed, "Sign-in failed")

    def _ask_for_client_file(self) -> bool:
        from ..gdocs import auth

        if QMessageBox.information(self, "One-time setup", SETUP_HELP,
                                   QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
            return False
        path, _ = QFileDialog.getOpenFileName(self, "Choose the Google client file", str(Path.home() / "Downloads"),
                                              "JSON files (*.json)")
        if not path:
            return False
        try:
            auth.install_client_file(path)
        except auth.AuthError as exc:
            QMessageBox.warning(self, "That file won't work", str(exc))
            return False
        return True

    def _signed_in(self, creds):
        self._set_creds(creds)
        self.raise_()
        self.activateWindow()

    def _sign_in_failed(self, message):
        self._set_creds(None)
        QMessageBox.warning(self, "Sign-in didn't work", message)

    # =========================================================================
    # Settings
    # =========================================================================
    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            reparse = dialog.apply()
            self.strip_colour.setChecked(self.settings.strip_colour)
            if reparse and self.doc_path:
                self.load(self.doc_path)
            elif self.doc:
                self.tree.set_document(self.doc, self.settings)  # heading levels may have changed
                self.refresh()

    # =========================================================================
    # File selection, outline and preview
    # =========================================================================
    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a study file", "", FILE_FILTER)
        if path:
            self.load(Path(path))

    def dragEnterEvent(self, event):
        if self._dropped_path(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = self._dropped_path(event)
        if path:
            self.load(path)

    @staticmethod
    def _dropped_path(event) -> Path | None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            p = Path(urls[0].toLocalFile())
            if p.suffix.lower() in SUPPORTED:
                return p
        return None

    def load(self, path: Path):
        self.file_label.setText(path.name)
        self.statusBar().showMessage(f"Reading {path.name}…")
        self.open_btn.setEnabled(False)
        settings = self.settings
        self._start(lambda _: (path, parse_file(path, settings)), self._parsed, self._parse_failed,
                    "Something went wrong reading this file:")

    def _parsed(self, outcome):
        path, doc = outcome
        self.open_btn.setEnabled(True)
        self.doc, self.doc_path = doc, path
        if doc.warnings:
            self.warning.setText("\n".join(doc.warnings))
            self.warning.show()
        else:
            self.warning.hide()
        for sb in (self.page_from, self.page_to):
            sb.blockSignals(True)
            sb.setRange(1, max(1, doc.page_count))
        self.page_from.setValue(1)
        self.page_to.setValue(max(1, doc.page_count))
        for sb in (self.page_from, self.page_to):
            sb.blockSignals(False)
        self._left_panel.show()
        self.stack.setCurrentIndex(1)
        self.tree.page_range = (1, max(1, doc.page_count))
        self.tree.set_document(doc, self.settings)   # also triggers refresh via selection_changed

    def _parse_failed(self, message: str):
        self.open_btn.setEnabled(True)
        self.statusBar().showMessage("Couldn't read that file")
        QMessageBox.warning(self, "Couldn't read that file", message)

    def _page_range_changed(self):
        if self.page_from.value() > self.page_to.value():
            sender = self.sender()
            other = self.page_to if sender is self.page_from else self.page_from
            other.blockSignals(True)
            other.setValue(sender.value())
            other.blockSignals(False)
        self.tree.set_page_range(self.page_from.value(), self.page_to.value())

    def _selection_changed(self):
        ticked, total = self.tree.counts()
        self.append_btn.setText(f"Append {ticked} item{'s' if ticked != 1 else ''}")
        doc = self.doc
        if doc:
            kind = "Slide deck" if doc.layout == "slides" else "Document"
            headings = sum(1 for b in doc.blocks if b.kind == "heading")
            self.statusBar().showMessage(
                f"{kind} · {doc.page_count} pages · {headings} headings · {ticked} of {total} items ticked")
        self.refresh()

    def refresh(self):
        if not self.doc:
            return
        self.settings.strip_colour = self.strip_colour.isChecked()
        scroll = self.preview.verticalScrollBar().value()
        html, images = document_html(self.doc, self.settings, show_notes=self.show_notes.isChecked(),
                                     show_skipped=self.show_skipped.isChecked())
        document = self.preview.document()
        for name, png in images.items():
            document.addResource(QTextDocument.ImageResource, QUrl(name), QImage.fromData(png, "PNG"))
        self.preview.setHtml(html)
        self.preview.verticalScrollBar().setValue(scroll)

    # =========================================================================
    # Choosing the Google Doc and where to insert
    # =========================================================================
    def _load_recent(self, select_url: str = ""):
        self.doc_combo.blockSignals(True)
        self.doc_combo.clear()
        for r in recent.load():
            self.doc_combo.addItem(r.title, r.url)
        idx = self.doc_combo.findData(select_url) if select_url else -1
        if idx >= 0:
            self.doc_combo.setCurrentIndex(idx)
        else:
            self.doc_combo.setCurrentIndex(-1)
            self.doc_combo.setEditText(select_url)
        self.doc_combo.blockSignals(False)

    def _current_doc_url(self) -> str:
        """The chosen recent doc's link, or whatever was pasted."""
        i = self.doc_combo.currentIndex()
        text = self.doc_combo.currentText().strip()
        if i >= 0 and text == self.doc_combo.itemText(i):
            return self.doc_combo.itemData(i)
        return text

    def _doc_changed(self, force: bool = False):
        from .. import gdocs

        doc_id = gdocs.doc_id_from_url(self._current_doc_url())
        if not doc_id:
            self.doc_info = None
            self._fill_insert_combo([])
            text = self.doc_combo.currentText().strip()
            self._set_doc_status("⚠ That isn’t a Google Doc link" if text else "", "#B3261E")
            return
        if not force and ((self.doc_info and self.doc_info.doc_id == doc_id) or self._loading_doc_id == doc_id):
            return
        if not self._creds:
            self._set_doc_status("Sign in to load this doc’s headings", "#5F6368")
            return
        creds = self._creds
        self._loading_doc_id = doc_id
        self._set_doc_status("Loading…", "#5F6368")
        self._start(lambda _: gdocs.fetch_doc_info(creds, doc_id), self._doc_loaded, self._doc_failed,
                    "Couldn't load the doc")

    def _doc_loaded(self, info):
        self._loading_doc_id = None
        self.doc_info = info
        self._set_doc_status(f"✓ {info.title}", "#137333")
        self._fill_insert_combo(info.headings)

    def _doc_failed(self, message):
        self._loading_doc_id = None
        self.doc_info = None
        self._fill_insert_combo([])
        self._set_doc_status("⚠ Can’t open this doc", "#B3261E")
        self.doc_status.setToolTip(message)

    def _set_doc_status(self, text: str, colour: str):
        self.doc_status.setText(text)
        self.doc_status.setToolTip("")
        self.doc_status.setStyleSheet(f"color: {colour};")

    def _fill_insert_combo(self, headings):
        previous = self.insert_combo.currentData()
        self.insert_combo.clear()
        self.insert_combo.addItem(END_OF_DOC, None)
        for h in headings:
            indent = "    " * max(0, h.level - 1)
            self.insert_combo.addItem(f"End of section:  {indent}{h.text[:90]}", h)
        if previous is not None:
            for i in range(1, self.insert_combo.count()):
                if self.insert_combo.itemData(i).text == previous.text:
                    self.insert_combo.setCurrentIndex(i)
                    break

    # =========================================================================
    # Append
    # =========================================================================
    def append(self):
        from .. import gdocs

        if not self.doc:
            QMessageBox.information(self, "No file yet", "Open a PDF, Word or PowerPoint file first.")
            return
        if not any(b.selected for b in self.doc.blocks):
            QMessageBox.information(self, "Nothing ticked", "Tick at least one item in the outline on the left.")
            return
        url = self._current_doc_url()
        doc_id = gdocs.doc_id_from_url(url)
        dry = self.dry_run.isChecked()
        if not doc_id and not dry:
            QMessageBox.information(self, "Which doc?", "Paste the link to your Google Doc into the box first.\n\n"
                                    "(Open the doc in your browser and copy the address from the address bar.)")
            return
        if not self._creds and not dry:
            QMessageBox.information(self, "Sign in first", "Sign in with Google (top right) first.")
            return

        settings = self.settings
        settings.strip_colour = self.strip_colour.isChecked()
        after = self.insert_combo.currentData()
        if doc_id:
            settings.last_doc_url = url
            settings.save()
        document, creds = self.doc, self._creds

        if dry:
            out, _ = QFileDialog.getSaveFileName(self, "Save the dry-run plan", str(Path.home() / "dry_run.json"),
                                                 "JSON files (*.json)")
            if not out:
                return
            def work(progress):
                return gdocs.dry_run(document, doc_id or "", settings, out, creds, progress, after), out, url
        else:
            def work(progress):
                return gdocs.append_document(document, doc_id, settings, creds, progress, after), None, url

        self.append_btn.setEnabled(False)
        self.progress.setRange(0, 0)  # busy until the first progress update
        self.progress.setFormat("Starting…")
        self.progress.show()
        self._start(work, self._appended, self._append_failed, "Appending failed.", self._append_progress)

    def _append_progress(self, done: int, total: int, message: str):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.progress.setFormat(message)

    def _appended(self, outcome):
        from .. import gdocs

        result, dry_run_file, url = outcome
        self.append_btn.setEnabled(True)
        self.progress.hide()
        if dry_run_file:
            self.statusBar().showMessage(f"Dry run saved to {dry_run_file}")
            QMessageBox.information(self, "Dry run saved",
                                    f"Nothing was changed. The planned changes were saved to:\n{dry_run_file}")
            return
        title = self.doc_info.title if self.doc_info and self.doc_info.doc_id == result.doc_id else "Google Doc"
        recent.remember(result.doc_id, title, url)
        self._load_recent(select_url=url)
        where = self.insert_combo.currentText()
        self.statusBar().showMessage(f"Appended to “{title}”")
        box = QMessageBox(self)
        box.setWindowTitle("Done")
        box.setText(f"Your notes were added to “{title}”.\n\n"
                    + ("At the end of the document." if where == END_OF_DOC else where.replace("  ", " ") + "."))
        open_btn = box.addButton("Open the doc", QMessageBox.AcceptRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl(gdocs.doc_url(result.doc_id)))
        self._doc_changed(force=True)  # headings changed

    def _append_failed(self, message: str):
        self.append_btn.setEnabled(True)
        self.progress.hide()
        self.statusBar().showMessage("Append failed")
        if "sign in again" in message.lower():
            from ..gdocs import auth
            auth.sign_out()
            self._set_creds(None)
        QMessageBox.warning(self, "Couldn't append", message)
