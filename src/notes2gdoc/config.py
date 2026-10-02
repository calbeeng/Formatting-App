"""User settings, stored as JSON in the per-user app-data folder."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "notes2gdoc"

# Allowed values in the heading map.
NAMED_STYLES = ("HEADING_1", "HEADING_2", "HEADING_3", "HEADING_4", "NORMAL_TEXT")

DEFAULT_HEADING_MAP = {
    "decimal": "HEADING_1",      # "1.", "2." ...
    "alpha": "HEADING_2",        # "(a)", "(b)" ...
    "roman": "HEADING_3",        # "(i)", "(ii)" ...
    "slide_title": "HEADING_2",  # title of each slide in a slide deck
    "section_title": "HEADING_1",   # section divider slide ("Pre-Acquisition Steps")
    "slide_subtitle": "HEADING_3",  # bold line just under a slide title
}


def config_dir() -> Path:
    """Per-user folder for settings, OAuth client file and token.

    Windows: %APPDATA%\\notes2gdoc   macOS: ~/Library/Application Support/notes2gdoc
    """
    return Path(user_config_dir(APP_NAME, appauthor=False, roaming=True))


@dataclass
class Settings:
    heading_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HEADING_MAP))
    # Remove all text colour from the inserted content.
    strip_colour: bool = False
    # Leave copyright/"Notice" boilerplate slides unticked by default.
    skip_boilerplate_slides: bool = True
    # The Google Doc link used last time, pre-filled in the app
    last_doc_url: str = ""
    # Justify all appended text (pictures are always centred)
    justify_text: bool = True

    def named_style(self, style_key: str | None) -> str:
        return self.heading_map.get(style_key or "", "NORMAL_TEXT")

    def heading_rank(self, style_key: str | None) -> int:
        """1 for HEADING_1, 2 for HEADING_2 ... 0 if the key maps to normal text."""
        style = self.named_style(style_key)
        return int(style.rsplit("_", 1)[1]) if style.startswith("HEADING_") else 0

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or config_dir() / "settings.json"
        settings = cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        heading_map = data.get("heading_map", {})
        for key, value in heading_map.items():
            if value in NAMED_STYLES:
                settings.heading_map[key] = value
        settings.strip_colour = bool(data.get("strip_colour", settings.strip_colour))
        settings.skip_boilerplate_slides = bool(
            data.get("skip_boilerplate_slides", settings.skip_boilerplate_slides)
        )
        settings.last_doc_url = str(data.get("last_doc_url", ""))
        settings.justify_text = bool(data.get("justify_text", settings.justify_text))
        return settings

    def save(self, path: Path | None = None) -> None:
        path = path or config_dir() / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
