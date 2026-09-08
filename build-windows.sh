#!/usr/bin/env bash
# Build PuTTY Directory on Windows, from Git Bash / MSYS.
#
#   ./build-windows.sh            -> dist/PuttyDirectory.exe  (single file)
#   ./build-windows.sh --onedir   -> dist/PuttyDirectory/     (folder, faster start)
#   ./build-windows.sh --clean    -> discard cached analysis first
#
# The counterpart to build-linux.sh. build.py runs the binary's own --selftest
# afterwards and fails the build if the icon or tray pipeline is broken, so a
# successful run here means a genuinely working executable, not just one that
# compiled.
#
# From PowerShell or cmd use build-windows.bat, or call `python build.py`
# directly - these wrappers only add environment setup.

set -euo pipefail
cd "$(dirname "$0")"

VENV="${PUTTYDIR_VENV:-.venv}"

fail() { printf '\n\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }
note() { printf '\033[36m==>\033[0m %s\n' "$1"; }

# --- platform ----------------------------------------------------------------
# PyInstaller does not cross-compile: a .exe has to be built on Windows. Running
# this under WSL would silently produce a Linux binary instead.

case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) ;;
    *) fail "this builds the Windows .exe and must run on Windows (Git Bash).
On Linux use ./build-linux.sh instead - PyInstaller cannot cross-compile." ;;
esac

# --- python ------------------------------------------------------------------

PYTHON=""
if command -v py >/dev/null 2>&1 && py -3 -c "" >/dev/null 2>&1; then
    PYTHON="py -3"
elif command -v python >/dev/null 2>&1 && python -c "" >/dev/null 2>&1; then
    PYTHON="python"
else
    fail "no working Python found.
Install Python 3.11+ from python.org (tick 'Add python.exe to PATH')."
fi

platform=$($PYTHON -c "import sys; print(sys.platform)" 2>/dev/null || echo unknown)
[ "$platform" = "win32" ] || fail "'$PYTHON' is not a Windows interpreter (sys.platform=$platform).
If you are in WSL, use ./build-linux.sh instead."

$PYTHON -c 'import tkinter' 2>/dev/null \
    || fail "tkinter is missing from this Python.
Re-run the python.org installer and enable 'tcl/tk and IDLE'."

note "$($PYTHON -c 'import platform; print("Python " + platform.python_version())'), tkinter ok"

VPY="$VENV/Scripts/python.exe"

# --- dependencies ------------------------------------------------------------
# This project is uv-managed. uv creates virtualenvs WITHOUT pip in them, so
# "can I run pip in there?" is NOT a valid health check - it reports a perfectly
# good uv venv as broken. Prefer uv when the lock file is present; it also
# reproduces exact versions, which pip install here would not.

if command -v uv >/dev/null 2>&1 && [ -f uv.lock ]; then
    note "installing dependencies with uv (uv.lock present)"
    UV_PROJECT_ENVIRONMENT="$VENV" uv sync --dev \
        || fail "uv sync failed. Delete $VENV and try again if it is in a bad state."
else
    if [ ! -d "$VENV" ]; then
        note "creating $VENV"
        $PYTHON -m venv "$VENV" || { rm -rf "$VENV"; fail "could not create a venv in $VENV."; }
    fi
    # Never delete an existing environment automatically - it may be managed by
    # something else, and a wrong guess costs real work. Say what is wrong and
    # let the caller decide.
    [ -x "$VPY" ] || fail "$VENV exists but has no Scripts/python.exe.
If it is a Linux venv copied across, or a failed creation, remove it and re-run:
    rm -rf $VENV"
    "$VPY" -m pip --version >/dev/null 2>&1 \
        || fail "$VENV has no pip, and no uv is available to manage it.
Either install uv (https://docs.astral.sh/uv/), or recreate the venv:
    rm -rf $VENV && ./build-windows.sh"

    note "installing dependencies with pip"
    "$VPY" -m pip install --quiet --upgrade pip
    # python-xlib is Linux-only; on Windows pystray uses its win32 backend.
    "$VPY" -m pip install --quiet pillow pystray pyinstaller
fi

[ -x "$VPY" ] || fail "no interpreter at $VPY after dependency setup."
"$VPY" -c "import PIL, pystray, PyInstaller" 2>/dev/null \
    || fail "the build dependencies are not importable from $VPY."

note "building"
"$VPY" build.py "$@"

cat <<'EOF'

Next steps
----------
    dist\PuttyDirectory.exe --tray       start it in the notification area
    dist\PuttyDirectory.exe --selftest   re-check the build at any time

To start it at login, press Win+R, run  shell:startup , and put a shortcut to
dist\PuttyDirectory.exe with the --tray argument in the folder that opens.

A new tray icon usually lands in the hidden-icons overflow (the ^ chevron);
drag it onto the taskbar to keep it visible.
EOF
