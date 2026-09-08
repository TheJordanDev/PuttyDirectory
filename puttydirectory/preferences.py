"""Application preferences, kept apart from any one directory file.

Two kinds of state got conflated before this existed:

* things about *the app* - where putty.exe is, whether closing hides to the
  tray, which files you opened recently
* things about *a directory of sessions* - the tree itself, and which of its
  sessions you connected to last

The first kind used to live inside whichever JSON was open, so opening a second
directory silently changed your PuTTY path. App-level settings now live here, in
one file, whatever directory is open. Per-file state stays in the file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .store import default_config_path

#: Settings that belong to the app, not to a directory file. Read from a
#: directory's settings once, for backward compatibility, then written here.
APP_KEYS = ("putty_path", "confirm_delete", "close_to_tray", "tray_hint_shown")

MAX_RECENT_FILES = 10


def preferences_path() -> Path:
    return default_config_path().parent / "preferences.json"


class Preferences:
    def __init__(self, path: Path, data: dict | None = None):
        self.path = Path(path)
        self.data: dict = data or {}

    @classmethod
    def load(cls, path: Path | None = None) -> "Preferences":
        path = Path(path) if path else preferences_path()
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            # Preferences are conveniences: a missing or corrupt file must never
            # stop the app, it just means defaults.
            data = {}
        return cls(path, data if isinstance(data, dict) else {})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2)
                handle.write("\n")
            os.replace(temp, self.path)
        except OSError:
            pass  # Losing preferences is not worth interrupting the user for.

    # -- plain settings ---------------------------------------------------

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()

    def adopt_legacy(self, settings: dict) -> bool:
        """Take app-level keys out of a directory file, once.

        Older directory files carry putty_path and friends. Copy anything we do
        not already know, so upgrading does not lose the PuTTY path.
        """
        changed = False
        for key in APP_KEYS:
            if key not in self.data and key in settings:
                self.data[key] = settings[key]
                changed = True
        if changed:
            self.save()
        return changed

    # -- recent files -----------------------------------------------------

    @property
    def recent_files(self) -> list[str]:
        files = self.data.get("recent_files", [])
        return [item for item in files if isinstance(item, str)]

    def remember_file(self, path: Path) -> None:
        resolved = str(Path(path).resolve())
        files = [item for item in self.recent_files if item != resolved]
        files.insert(0, resolved)
        self.data["recent_files"] = files[:MAX_RECENT_FILES]
        self.data["last_file"] = resolved
        self.save()

    def forget_file(self, path: Path) -> None:
        resolved = str(Path(path).resolve())
        self.data["recent_files"] = [item for item in self.recent_files if item != resolved]
        if self.data.get("last_file") == resolved:
            self.data.pop("last_file", None)
        self.save()

    def clear_recent_files(self) -> None:
        self.data["recent_files"] = []
        self.save()

    @property
    def last_file(self) -> Path | None:
        value = self.data.get("last_file")
        return Path(value) if value else None
