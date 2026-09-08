"""Finding the PuTTY binary and launching a session with it."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS_CANDIDATES = (
    r"C:\Program Files\PuTTY\putty.exe",
    r"C:\Program Files (x86)\PuTTY\putty.exe",
)

POSIX_CANDIDATES = (
    "/usr/bin/putty",
    "/usr/local/bin/putty",
    "/snap/bin/putty",
)


class LaunchError(Exception):
    pass


def find_putty(configured: str = "") -> str | None:
    """Locate putty: the configured path, then $PATH, then the usual places."""
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.exists() else None

    found = shutil.which("putty") or shutil.which("putty.exe")
    if found:
        return found

    candidates = WINDOWS_CANDIDATES if sys.platform == "win32" else POSIX_CANDIDATES
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def build_command(putty: str, config: dict) -> list[str]:
    """Turn resolved session values into a PuTTY argument list."""
    host = (config.get("host") or "").strip()
    saved = (config.get("putty_session") or "").strip()
    if not host and not saved:
        raise LaunchError("This session has no host and no saved PuTTY session to load.")

    command = [putty]

    # -load first: later flags then override the saved session's settings.
    if saved:
        command += ["-load", saved]

    protocol = (config.get("protocol") or "ssh").strip()
    if host:
        command.append(f"-{protocol}")

    port = str(config.get("port") or "").strip()
    if port:
        if not port.isdigit():
            raise LaunchError(f"Port must be a number, got {port!r}.")
        command += ["-P", port]

    user = (config.get("user") or "").strip()
    if user:
        command += ["-l", user]

    key_file = (config.get("key_file") or "").strip()
    if key_file:
        expanded = Path(key_file).expanduser()
        if not expanded.exists():
            raise LaunchError(f"Private key file not found:\n{expanded}")
        command += ["-i", str(expanded)]

    password = config.get("password") or ""
    if password:
        command += ["-pw", password]

    extra = (config.get("extra_args") or "").strip()
    if extra:
        command += shlex.split(extra, posix=sys.platform != "win32")

    if host:
        command.append(host)
    return command


def redact(command: list[str]) -> str:
    """A copy-pasteable command line with the password masked."""
    safe = list(command)
    for index, argument in enumerate(safe):
        if argument == "-pw" and index + 1 < len(safe):
            safe[index + 1] = "********"
    quote = (lambda part: f'"{part}"' if " " in part else part) if sys.platform == "win32" else shlex.quote
    return " ".join(quote(part) for part in safe)


def launch(command: list[str]) -> subprocess.Popen:
    """Start PuTTY detached, so it outlives this app."""
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(command, close_fds=True, **kwargs)
    except OSError as error:
        raise LaunchError(f"Could not start PuTTY:\n{error}") from error
