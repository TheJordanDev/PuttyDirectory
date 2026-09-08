"""Loading and saving a directory file.

The directory lives in its own JSON file, entirely separate from PuTTY's own
saved sessions (the Windows registry, or ~/.putty on Linux). Point the app at a
different file to keep, say, work and personal sets apart:

    python main.py --config ~/work-sessions.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .model import Directory

ENV_VAR = "PUTTYDIR_CONFIG"
LEGACY_FILE = Path("config.json")


def default_config_path() -> Path:
    """The per-user directory file, in the platform's usual config location."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "PuttyDirectory" / "directory.json"


def resolve_config_path(explicit: str | None = None) -> Path:
    """Pick the file to use: --config, then $PUTTYDIR_CONFIG, then defaults."""
    if explicit:
        return Path(explicit).expanduser()
    from_env = os.environ.get(ENV_VAR)
    if from_env:
        return Path(from_env).expanduser()
    # Keep using a config.json sitting next to the app if one is already there.
    if LEGACY_FILE.exists():
        return LEGACY_FILE.resolve()
    return default_config_path()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> Directory:
        if not self.path.exists():
            return Directory()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError(f"Could not read {self.path}:\n{error}") from error
        return Directory.from_dict(data)

    def save(self, directory: Directory) -> None:
        """Write atomically, so a crash mid-save cannot truncate the file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(directory.to_dict(), handle, indent=2)
                handle.write("\n")
            os.replace(temp, self.path)
        except OSError as error:
            temp.unlink(missing_ok=True)
            raise StoreError(f"Could not write {self.path}:\n{error}") from error


class StoreError(Exception):
    pass
