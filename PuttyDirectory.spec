# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build definition.

Build through ``python build.py`` rather than calling PyInstaller directly -
it generates the icon this spec expects. Set PUTTYDIR_ONEDIR=1 for a folder
build instead of a single file (``build.py --onedir`` does that for you).

The excludes below are not guesswork; each was measured against a default
build. See README-BUILD.md for the numbers and why each one is safe.
"""

import os
import sys
from pathlib import Path

ONEDIR = os.environ.get("PUTTYDIR_ONEDIR") == "1"
ROOT = Path(SPECPATH)
ICON = ROOT / "assets" / "icon.ico"

# pystray picks its backend at import time, so the one we need is invisible to
# static analysis and has to be named explicitly.
#
# On Linux there are three candidates with very different costs. Naming all of
# them would make PyInstaller drag in PyGObject whenever it happens to be
# installed, which bloats the bundle and is notoriously fragile to freeze. So we
# include only backends that actually import here, preferring the one set in
# PYSTRAY_BACKEND. _xorg needs python-xlib, which is a plain pip package and
# freezes cleanly - it is the reliable choice for a portable Linux build.
def _installed(module: str) -> bool:
    """Is a top-level module importable?

    Deliberately probes each backend's dependency (Xlib, gi) rather than the
    backend module itself: importing ``pystray`` runs its own backend selection
    and raises when none is usable, which would tell us nothing.
    """
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


if sys.platform == "win32":
    BACKENDS = ["pystray._win32"]
elif sys.platform == "darwin":
    BACKENDS = ["pystray._darwin"]
else:
    forced = os.environ.get("PYSTRAY_BACKEND")
    if forced:
        BACKENDS = [f"pystray._{forced}"]
    else:
        BACKENDS = []
        if _installed("Xlib"):
            BACKENDS.append("pystray._xorg")
        if _installed("gi"):
            BACKENDS += ["pystray._appindicator", "pystray._gtk"]
        if not BACKENDS:
            print("WARNING: neither python-xlib nor PyGObject is installed, so "
                  "the build will have no tray icon. pip install python-xlib")

EXCLUDES = [
    # --- Pillow codecs we never touch. We only draw an icon and save PNG/ICO.
    # Every one of these is imported behind try/except inside Pillow, so their
    # absence degrades gracefully instead of raising. _avif alone is 7.5 MB.
    "PIL._avif",
    "PIL._imagingft",
    "PIL._webp",
    "PIL._imagingcms",
    "PIL._imagingmath",
    "PIL._imagingmorph",
    "PIL.ImageQt",
    "PIL.ImageShow",
    # --- OpenSSL. The app makes no network connections; _hashlib is the only
    # thing pulling in libcrypto/libssl (6.5 MB). hashlib falls back to the
    # built-in _sha* modules, and random.py prefers those anyway.
    "_ssl",
    "ssl",
    "_hashlib",
    # --- Stdlib we do not import.
    "email",
    "http",
    # NOT urllib: pathlib imports urllib.parse for Path.as_uri(). Excluding the
    # package breaks pathlib, and therefore the whole app, at startup.
    "xml",
    "xmlrpc",
    "html",
    "unittest",
    "doctest",
    "pydoc",
    "pydoc_data",
    "pdb",
    "sqlite3",
    "bz2",
    "_bz2",
    "lzma",
    "_lzma",
    "multiprocessing",
    "asyncio",
    "concurrent",
    "decimal",
    "_decimal",
    "_pydecimal",
    "statistics",
    "curses",
    "readline",
    "lib2to3",
    "test",
    "tkinter.test",
    # --- Build-time only, never needed at runtime.
    "distutils",
    "setuptools",
    "pkg_resources",
    "pip",
    "numpy",
    "pytest",
]


a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=BACKENDS,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=2,  # -OO: drop docstrings and asserts from the bundled bytecode.
)


def _wanted(entry):
    """Drop Tcl/Tk data the GUI never reads.

    tzdata is 609 files / 1.3 MB of timezone tables for Tcl's ``clock``
    command; msgs are Tk's UI translations; tcl8/ holds the http and tdbc
    packages. None are reachable from a Tkinter widget.
    """
    dest = str(entry[0]).replace("\\", "/")
    unwanted = ("/tzdata/", "tcl8/", "/msgs/", "/tzdata", "/http1.0/", "/opt0.4/")
    return not any(part in dest for part in unwanted)


a.datas = [entry for entry in a.datas if _wanted(entry)]

pyz = PYZ(a.pure)

common = dict(
    name="PuttyDirectory",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,  # No effect on Windows; keep symbols for readable tracebacks.
    upx=False,    # UPX trips antivirus heuristics; see README-BUILD.md.
    console=False,  # GUI app - never flash a console window.
    disable_windowed_traceback=False,
    # icon= is only honoured for PE and Mach-O; an ELF has no embedded icon
    # (the desktop file supplies it there instead).
    icon=str(ICON) if ICON.exists() and sys.platform in ("win32", "darwin") else None,
    version=str(ROOT / "assets" / "version_info.txt")
    if (ROOT / "assets" / "version_info.txt").exists() and sys.platform == "win32"
    else None,
)

if ONEDIR:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="PuttyDirectory",
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **common)
