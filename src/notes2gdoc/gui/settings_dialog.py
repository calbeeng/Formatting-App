"""Settings window: which Google Docs style each kind of source heading gets,
and a couple of defaults. Saved to settings.json in the app-data folder."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
)

from ..config import DEFAULT_HEADING_MAP, Settings

# Shown in this order, with an example of each
HEADING_KINDS = [
    ("decimal", "Numbered headings, e.g. “1. LIQUIDATION”"),
    ("alpha", "Lettered headings, e.g. “(a) Overview”"),
    ("roman", "Roman numeral headings, e.g. “(ii) Presumption…”"),
    ("section_title", "Section divider slides, e.g. “Pre-Acquisition Steps”"),
    ("slide_title", "Slide titles"),
    ("slide_subtitle", "Bold line under a slide title"),
    ("word_h1", "Word “Heading 1”"),
    ("word_h2", "Word “Heading 2”"),
    ("word_h3", "Word “Heading 3”"),
]
STYLE_CHOICES = [
    ("Heading 1", "HEADING_1"),
    ("Heading 2", "HEADING_2"),
    ("Heading 3", "HEADING_3"),
    ("Heading 4", "HEADING_4"),
    ("Normal text", "NORMAL_TEXT"),
]


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.settings = settings
        layout = QVBoxLayout(self)

        group = QGroupBox("Which Google Docs style should each kind of heading use?")
        form = QFormLayout(group)
        self.combos: dict[str, QComboBox] = {}
        for key, label in HEADING_KINDS:
            combo = QComboBox()
            for text, value in STYLE_CHOICES:
                combo.addItem(text, value)
            combo.setCurrentIndex(combo.findData(settings.heading_map.get(key, DEFAULT_HEADING_MAP[key])))
            self.combos[key] = combo
            form.addRow(label, combo)
        layout.addWidget(group)

        note = QLabel("Fonts, sizes and spacing always come from your Google Doc's own heading styles.")
        note.setStyleSheet("color: #5F6368;")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.strip_colour = QCheckBox("Strip text colour by default")
        self.strip_colour.setChecked(settings.strip_colour)
        self.skip_boilerplate = QCheckBox("Leave out copyright / “Notice” slides by default")
        self.skip_boilerplate.setChecked(settings.skip_boilerplate_slides)
        self.justify = QCheckBox("Justify text (pictures are always centred)")
        self.justify.setChecked(settings.justify_text)
        layout.addWidget(self.justify)
        layout.addWidget(self.strip_colour)
        layout.addWidget(self.skip_boilerplate)

        reset = QDialogButtonBox.RestoreDefaults
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel | reset)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(reset).clicked.connect(self._defaults)
        layout.addWidget(buttons)

    def _defaults(self):
        for key, combo in self.combos.items():
            combo.setCurrentIndex(combo.findData(DEFAULT_HEADING_MAP[key]))
        self.strip_colour.setChecked(False)
        self.skip_boilerplate.setChecked(True)
        self.justify.setChecked(True)

    def apply(self) -> bool:
        """Copy the choices into settings and save. Returns True if the file
        needs re-reading (the boilerplate-slide default changed)."""
        for key, combo in self.combos.items():
            self.settings.heading_map[key] = combo.currentData()
        self.settings.strip_colour = self.strip_colour.isChecked()
        self.settings.justify_text = self.justify.isChecked()
        reparse = self.settings.skip_boilerplate_slides != self.skip_boilerplate.isChecked()
        self.settings.skip_boilerplate_slides = self.skip_boilerplate.isChecked()
        self.settings.save()
        return reparse
