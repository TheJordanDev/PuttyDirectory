"""Reading PuTTY's own saved sessions, so they can be imported.

Strictly read-only: nothing here writes to the registry or to ~/.putty. Importing
copies a session in, it never removes it from PuTTY.

PuTTY keeps sessions in:

* Windows - ``HKCU\\Software\\SimonTatham\\PuTTY\\Sessions\\<name>``
* Unix    - ``~/.putty/sessions/<name>``, as ``Key=Value`` lines

In both places the session *name* is escaped by PuTTY's ``mungestr``: spaces,
backslash, asterisk, question mark, percent, control characters, anything above
``~``, and a leading dot all become ``%XX``. So "Homelab - Ubuntu" is stored as
``Homelab%20-%20Ubuntu`` and has to be decoded for display.
"""

from __future__ import annotations

import os
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .model import PROTOCOLS, SESSION, Node

REGISTRY_PATH = r"Software\SimonTatham\PuTTY\Sessions"


def unix_session_dirs() -> list[Path]:
    """Every directory a PuTTY build might keep Unix sessions in.

    ``putty(1)`` documents ``~/.putty/sessions``, and that is what stock builds
    use. Two others are checked as well because betting on a single path means
    silently importing nothing when a build or distro differs: ``$PUTTYDIR``
    (which some builds honour as a config-root override) and an XDG-style
    location. Searching all of them costs nothing; guessing wrong costs the
    whole feature.
    """
    candidates = []

    override = os.environ.get("PUTTYDIR")
    if override:
        candidates.append(Path(override).expanduser() / "sessions")

    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    candidates.append(base / "putty" / "sessions")

    candidates.append(Path.home() / ".putty" / "sessions")
    return candidates

#: PuTTY's own default-settings pseudo-session. It holds no host, so it is not
#: a connection anyone would want imported.
DEFAULT_SESSION = "Default Settings"

INTERESTING = ("HostName", "PortNumber", "Protocol", "UserName", "PublicKeyFile")


def unmunge(name: str) -> str:
    """Undo PuTTY's ``mungestr`` escaping of a session name."""
    out: list[str] = []
    index = 0
    while index < len(name):
        if name[index] == "%" and index + 2 < len(name) + 1:
            pair = name[index + 1:index + 3]
            if len(pair) == 2 and all(char in string.hexdigits for char in pair):
                out.append(chr(int(pair, 16)))
                index += 3
                continue
        out.append(name[index])
        index += 1
    return "".join(out)


@dataclass
class PuttySession:
    """One of PuTTY's saved sessions, in our terms."""

    name: str
    host: str = ""
    user: str = ""
    port: str = ""
    protocol: str = ""
    key_file: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def needs_load(self) -> bool:
        """No host of its own, so it can only work via ``putty -load``."""
        return not self.host

    def describe(self) -> str:
        if self.needs_load:
            return "(loads PuTTY session)"
        user = f"{self.user}@" if self.user else ""
        port = f":{self.port}" if self.port and self.port != "22" else ""
        return f"{user}{self.host}{port}"

    def to_node(self, keep_putty_session: bool = False) -> Node:
        """Build a directory node from this session."""
        node = Node(
            type=SESSION,
            name=self.name,
            host=self.host,
            user=self.user,
            port="" if self.port == "22" else self.port,
            protocol="" if self.protocol in ("", "ssh") else self.protocol,
            key_file=self.key_file,
            notes="Imported from PuTTY",
        )
        # A session with no host of its own is useless unless PuTTY supplies it,
        # so those always keep the -load reference regardless of the option.
        if keep_putty_session or self.needs_load:
            node.putty_session = self.name
        return node


def _from_values(name: str, values: dict) -> PuttySession:
    host = str(values.get("HostName", "") or "").strip()
    user = str(values.get("UserName", "") or "").strip()

    # PuTTY stores what you typed in its Host Name box, and people habitually
    # type user@host there - which leaves UserName empty. Split it out so the
    # user lands in its own field and folder inheritance can override it.
    if not user and "@" in host:
        user, host = host.rsplit("@", 1)

    port = values.get("PortNumber", "")
    port = str(int(port)) if isinstance(port, int) else str(port or "").strip()

    protocol = str(values.get("Protocol", "") or "").strip().lower()
    if protocol not in PROTOCOLS:
        protocol = ""  # serial and anything unknown fall back to our default

    return PuttySession(
        name=name,
        host=host,
        user=user,
        port=port,
        protocol=protocol,
        key_file=str(values.get("PublicKeyFile", "") or "").strip(),
        raw=values,
    )


def _read_registry() -> list[PuttySession]:
    import winreg

    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH)
    except OSError:
        return []

    sessions = []
    with root:
        count = winreg.QueryInfoKey(root)[0]
        for index in range(count):
            try:
                raw_name = winreg.EnumKey(root, index)
                with winreg.OpenKey(root, raw_name) as sub:
                    values = {}
                    for value_index in range(winreg.QueryInfoKey(sub)[1]):
                        key, value, _type = winreg.EnumValue(sub, value_index)
                        if key in INTERESTING:
                            values[key] = value
            except OSError:
                continue  # A session we cannot read should not stop the rest.
            sessions.append(_from_values(unmunge(raw_name), values))
    return sessions


def _read_session_file(path: Path) -> dict:
    """Parse one Unix session file: plain ``Key=Value`` lines."""
    values: dict = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if key in INTERESTING:
            values[key] = value.strip()
    return values


def _read_unix(directories: list[Path] | None = None) -> list[PuttySession]:
    sessions: list[PuttySession] = []
    seen: set[str] = set()

    for directory in (directories if directories is not None else unix_session_dirs()):
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if not entry.is_file():
                continue
            name = unmunge(entry.name)
            if name in seen:
                continue  # Earlier candidate directories win.
            try:
                values = _read_session_file(entry)
            except OSError:
                continue
            seen.add(name)
            sessions.append(_from_values(name, values))
    return sessions


def read_sessions(include_default: bool = False) -> list[PuttySession]:
    """Every PuTTY session on this machine, sorted by name."""
    sessions = _read_registry() if sys.platform == "win32" else _read_unix()
    if not include_default:
        sessions = [item for item in sessions if item.name != DEFAULT_SESSION]
    return sorted(sessions, key=lambda item: item.name.lower())


def where_it_looked() -> str:
    """Explain where sessions were searched for, for an empty-result message."""
    if sys.platform == "win32":
        return f"HKEY_CURRENT_USER\\{REGISTRY_PATH}"
    return "\n".join(str(item) for item in unix_session_dirs())
