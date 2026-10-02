"""The last 10 Google Docs you appended to, stored in the app-data folder
(recent.json), so you can pick them from a list instead of pasting links."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import config_dir

MAX_RECENT = 10


@dataclass
class RecentDoc:
    doc_id: str
    title: str
    url: str
    last_used: float = 0.0


def _path() -> Path:
    return config_dir() / "recent.json"


def load(path: Path | None = None) -> list[RecentDoc]:
    try:
        data = json.loads((path or _path()).read_text(encoding="utf-8"))
        items = [RecentDoc(**d) for d in data if isinstance(d, dict)]
    except (OSError, ValueError, TypeError):
        return []
    return sorted(items, key=lambda r: r.last_used, reverse=True)[:MAX_RECENT]


def remember(doc_id: str, title: str, url: str, path: Path | None = None) -> list[RecentDoc]:
    """Put this doc at the top of the list (updating its title)."""
    items = [r for r in load(path) if r.doc_id != doc_id]
    items.insert(0, RecentDoc(doc_id, title, url, time.time()))
    items = items[:MAX_RECENT]
    p = path or _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([asdict(r) for r in items], indent=2), encoding="utf-8")
    return items
