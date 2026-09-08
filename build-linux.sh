#!/usr/bin/env bash
# Build PuTTY Directory on Linux.
#
#   ./build-linux.sh            -> dist/PuttyDirectory  (single file)
#   ./build-linux.sh --onedir   -> dist/PuttyDirectory/ (folder, starts faster)
#
# Run this ON the machine you intend to run the app on, or on one with a glibc
# no newer than that machine's. PyInstaller links against the build host's
# glibc, so a binary built on a newer distro will not start on an older one.

set -euo pipefail
cd "$(dirname "$0")"

# Kept separate from the Windows .venv so the project folder can be shared
# between the two without either side clobbering the other's environment.
VENV=.venv-linux

fail() { printf '\n\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }
note() { printf '\033[36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$1"; }

APT_HINT="  Debian/Ubuntu:  sudo apt install python3 python3-venv python3-pip python3-tk
  Fedora:         sudo dnf install python3 python3-tkinter
  Arch:           sudo pacman -S python tk"

# --- prerequisites -----------------------------------------------------------
# All checked up front. PyInstaller will happily produce a bundle that only
# fails once you run it, and a half-built venv is worse than none.

command -v python3 >/dev/null 2>&1 || fail "python3 is not installed.
$APT_HINT"

python3 -c 'import tkinter' 2>/dev/null || fail "tkinter is missing.
It is packaged separately from python3 on most distros:
$APT_HINT"

# Debian and Ubuntu strip ensurepip out of the stdlib and ship it in
# python3-venv. Without it 'python3 -m venv' creates bin/ and then dies, leaving
# a venv with no pip - which is the confusing failure this check exists to
# prevent.
python3 -c 'import ensurepip' 2>/dev/null || fail "the venv module cannot create
environments: 'ensurepip' is missing.

  sudo apt install python3-venv

(Debian and Ubuntu split it out of the standard library. Without it,
'python3 -m venv' leaves a broken environment with no pip in it.)"

note "python3 $(python3 -c 'import platform; print(platform.python_version())'), tkinter ok"
note "glibc $(ldd --version 2>/dev/null | head -1 | awk '{print $NF}')"

# --- virtualenv --------------------------------------------------------------

venv_usable() {
    [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -m pip --version >/dev/null 2>&1
}

# Never delete an existing environment automatically. A uv-managed venv has no
# pip in it by design, so this check can call a perfectly good environment
# broken - and a wrong guess here destroys real work. Report and stop instead.
if [ -d "$VENV" ] && ! venv_usable; then
    fail "$VENV exists but has no working pip.
If uv created it, build with uv instead:  uv sync --dev && python build.py
Otherwise remove it and re-run:
    rm -rf $VENV"
fi

if [ ! -d "$VENV" ]; then
    note "creating $VENV"
    # Clean up a partial venv so a failed run cannot poison the next one.
    python3 -m venv "$VENV" || { rm -rf "$VENV"; fail "could not create a venv.
Install python3-venv and try again."; }
fi

if ! venv_usable; then
    note "repairing pip in $VENV"
    "$VENV/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
venv_usable || fail "$VENV still has no pip. Install python3-venv, then:
  rm -rf $VENV && ./build-linux.sh"

# shellcheck disable=SC1091
source "$VENV/bin/activate"

note "installing dependencies"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet pillow pystray python-xlib pyinstaller

# --- tray backend ------------------------------------------------------------
# pystray needs one of: python-xlib (X11), or PyGObject + AppIndicator. Only the
# first is pip-installable, so it is what we bundle. It talks X11, which works
# natively on X sessions and through XWayland on Wayland ones.

python -c 'import Xlib' 2>/dev/null \
    || warn "python-xlib missing - the build will have no tray icon."

note "building"
python build.py "$@"

cat <<EOF

Next steps
----------
    ./install-linux.sh --autostart    # into ~/.local, in the tray at login
    ./install-linux.sh --uninstall    # remove it again

If no tray icon appears, see the Linux section of README-BUILD.md - on GNOME
this is expected without the AppIndicator extension.
EOF
