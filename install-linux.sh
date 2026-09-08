#!/usr/bin/env bash
# Install a built PuTTY Directory into the current user's home.
#
#   ./install-linux.sh              install, no autostart
#   ./install-linux.sh --autostart  also start it in the tray at login
#   ./install-linux.sh --uninstall  remove everything this script installed
#
# Everything goes under ~/.local and ~/.config - no root, nothing system-wide.

set -euo pipefail
cd "$(dirname "$0")"

BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
AUTOSTART_DIR="$HOME/.config/autostart"

TARGET="$BIN_DIR/puttydirectory"
DESKTOP="$APP_DIR/puttydirectory.desktop"
ICON="$ICON_DIR/puttydirectory.png"
AUTOSTART="$AUTOSTART_DIR/puttydirectory.desktop"

note() { printf '\033[36m==>\033[0m %s\n' "$1"; }
fail() { printf '\n\033[31merror:\033[0m %s\n' "$1" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
    rm -fv "$TARGET" "$DESKTOP" "$ICON" "$AUTOSTART"
    rm -rfv "$BIN_DIR/PuttyDirectory"
    note "uninstalled"
    exit 0
fi

# Accept either build layout: dist/PuttyDirectory (onefile) or the onedir folder.
if [ -f dist/PuttyDirectory ]; then
    LAYOUT=onefile
elif [ -x dist/PuttyDirectory/PuttyDirectory ]; then
    LAYOUT=onedir
else
    fail "no build found. Run ./build-linux.sh first."
fi

mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR"

if [ "$LAYOUT" = onefile ]; then
    install -m 755 dist/PuttyDirectory "$TARGET"
else
    # The folder build must stay intact; install it whole and link the entry point.
    rm -rf "$BIN_DIR/PuttyDirectory"
    cp -r dist/PuttyDirectory "$BIN_DIR/PuttyDirectory"
    ln -sf "$BIN_DIR/PuttyDirectory/PuttyDirectory" "$TARGET"
fi
note "binary  -> $TARGET ($LAYOUT)"

install -m 644 dist/puttydirectory.png "$ICON"
note "icon    -> $ICON"

# Rewrite Exec to the installed location rather than reusing the build path.
sed "s|^Exec=.*|Exec=$TARGET --tray|" dist/puttydirectory.desktop > "$DESKTOP"
chmod 644 "$DESKTOP"
note "desktop -> $DESKTOP"

if [ "${1:-}" = "--autostart" ]; then
    mkdir -p "$AUTOSTART_DIR"
    cp "$DESKTOP" "$AUTOSTART"
    note "autostart -> $AUTOSTART"
fi

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) printf '\n\033[33mnote:\033[0m %s is not on your PATH; add it to run "puttydirectory" directly.\n' "$BIN_DIR" ;;
esac

cat <<EOF

Installed. Try it:

    $TARGET --list      # print your sessions
    $TARGET --tray      # start in the notification area

EOF
