"""Notification-area (system tray) icon, in the style of Pageant.

Right-click the icon for the whole session tree as nested submenus; click a
session to launch it. Left-click (or double-click) opens the manager window.

The tray needs ``pystray`` and ``Pillow``. When they are missing the app still
runs as a plain window - ``is_available()`` reports which.
"""

from __future__ import annotations

import base64
import io
import os
import sys
import threading
import traceback
from typing import Callable

from .model import Node

#: Set PUTTYDIR_DEBUG=1 to see why the icon or tray failed. A windowed build has
#: no console, so these paths are otherwise completely silent - which is exactly
#: what makes "no tray icon" so hard to diagnose.
DEBUG = bool(os.environ.get("PUTTYDIR_DEBUG"))

if os.environ.get("PUTTYDIR_DEBUG") == "verbose":
    # Pillow logs plugin-registration failures at debug level and otherwise
    # swallows them, which is what hid the missing PNG/ICO codecs. Very noisy -
    # it also logs every plugin it loads - so it is opt-in beyond plain DEBUG.
    import logging

    logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                        format="[%(name)s] %(message)s")


def debug(message: str, error: BaseException | None = None) -> None:
    """Report a swallowed failure, when there is anywhere to report it to."""
    if not DEBUG or sys.stderr is None:
        return
    try:
        print(f"[puttydirectory] {message}", file=sys.stderr, flush=True)
        if error is not None:
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
    except Exception:
        pass

try:  # Pillow only draws the image; it never touches the display.
    from PIL import Image, ImageDraw

    PIL_ERROR: Exception | None = None
except ImportError as error:  # pragma: no cover
    Image = ImageDraw = None
    PIL_ERROR = error

MAX_RECENT = 5

pystray = None
_TRAY_ERROR: Exception | None = None
_probed = False


def _probe() -> None:
    """Import pystray once, tolerating far more than ImportError.

    pystray picks *and initialises* its backend at import time - the X11 backend
    opens a display in ``_xorg.py`` at module scope. With no usable display that
    raises Xlib.error.DisplayNameError; the GTK backends raise their own errors.
    None of them are ImportError, so catching only that let them escape and take
    the whole app down. A tray that cannot start must degrade to a plain window,
    which is why this catches Exception.
    """
    global pystray, _TRAY_ERROR, _probed
    if _probed:
        return
    _probed = True
    if PIL_ERROR is not None:
        _TRAY_ERROR = PIL_ERROR
        return
    try:
        import pystray as module
    except Exception as error:  # noqa: BLE001 - deliberate, see docstring
        _TRAY_ERROR = error
        return
    pystray = module


def can_draw_icon() -> bool:
    """Pillow is usable, so the window icon renders even when the tray cannot."""
    return PIL_ERROR is None


def is_available() -> bool:
    _probe()
    return pystray is not None


def unavailable_reason() -> str:
    _probe()
    error = _TRAY_ERROR
    if error is None:
        return "The tray is available."
    if isinstance(error, ImportError):
        return (f"{error}\n\nInstall the tray dependencies:\n"
                f"  pip install pystray pillow python-xlib")
    if "display" in str(error).lower():
        return (
            f"{error}\n\n"
            "No usable X display, so no tray icon. A tray needs a running\n"
            "graphical session:\n"
            "  - start it from inside your desktop, not over SSH or a console\n"
            "  - on Wayland, XWayland must be present, or install PyGObject to\n"
            "    use the AppIndicator backend instead of X11\n"
            "  - autostarting via ~/.config/autostart gets this right for you"
        )
    return str(error)


def _diagnose_pillow() -> None:
    """Report why Pillow cannot save, when PUTTYDIR_DEBUG is set."""
    if not DEBUG or PIL_ERROR is not None:
        return
    debug(f"PIL.__file__      = {getattr(Image, '__file__', '?')}")
    debug(f"PIL._plugins      = {len(getattr(__import__('PIL'), '_plugins', []))} entries")
    for name in ("PngImagePlugin", "IcoImagePlugin", "BmpImagePlugin"):
        try:
            __import__(f"PIL.{name}")
            debug(f"import PIL.{name}: ok")
        except Exception as error:
            debug(f"import PIL.{name}: FAILED", error)
    try:
        Image.init()
        debug(f"Image.SAVE keys   = {sorted(Image.SAVE)}")
        debug(f"Image._initialized= {Image._initialized}")
    except Exception as error:
        debug("Image.init() raised", error)


def make_image(size: int = 64):
    """Draw the icon: a dark terminal tile with a green prompt."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    unit = size / 64
    draw.rounded_rectangle(
        (2 * unit, 2 * unit, size - 2 * unit, size - 2 * unit),
        radius=10 * unit, fill=(30, 30, 46, 255), outline=(90, 90, 120, 255),
        width=max(1, int(2 * unit)),
    )
    green = (126, 217, 87, 255)
    width = max(2, int(5 * unit))
    # A ">" chevron.
    draw.line([(18 * unit, 20 * unit), (30 * unit, 32 * unit)], fill=green, width=width)
    draw.line([(30 * unit, 32 * unit), (18 * unit, 44 * unit)], fill=green, width=width)
    # The cursor underscore.
    draw.line([(34 * unit, 44 * unit), (47 * unit, 44 * unit)], fill=green, width=width)
    return image


def icon_photo_data() -> str:
    """Base64 PNG of the icon, for ``tk.PhotoImage(data=...)``."""
    buffer = io.BytesIO()
    make_image(64).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class Tray:
    """Owns the pystray icon and keeps its menu in step with the directory.

    pystray runs its own event loop, so the icon lives on a background thread
    and every callback is marshalled back onto the Tk thread by ``dispatch``.
    """

    def __init__(self, app, dispatch: Callable[[Callable[[], None]], None]):
        self.app = app
        self.dispatch = dispatch
        self.icon = None
        self.error: BaseException | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> bool:
        if not is_available():
            debug(f"tray unavailable: {unavailable_reason()}")
            return False
        try:
            self.icon = pystray.Icon(
                "puttydirectory",
                icon=make_image(),
                title="PuTTY Directory",
                menu=self.build_menu(),
            )
        except Exception as error:
            # Drawing the image or building the menu can fail on its own - most
            # often a Pillow codec missing from a frozen build.
            debug("could not create the tray icon", error)
            self.error = error
            self.icon = None
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name="tray")
        self._thread.start()
        return True

    def _run(self) -> None:  # pragma: no cover - needs a real tray
        try:
            self.icon.run()
        except Exception as error:
            # A missing or broken tray (no AppIndicator, no X session) must not
            # take the app down with it; the window still works.
            debug("the tray icon stopped", error)
            self.error = error
            self.icon = None

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None

    def refresh(self) -> None:
        """Rebuild the menu after the directory changed."""
        if self.icon is None:
            return
        try:
            self.icon.menu = self.build_menu()
            self.icon.update_menu()
        except Exception:
            pass

    def notify(self, message: str, title: str = "PuTTY Directory") -> None:
        if self.icon is None:
            return
        try:
            self.icon.notify(message, title)
        except Exception:
            pass  # Not every backend implements notifications.

    # -- menu -------------------------------------------------------------

    def build_menu(self):
        items = []

        recent = self._recent_items()
        if recent:
            items.append(pystray.MenuItem("Recent", pystray.Menu(*recent)))
            items.append(pystray.Menu.SEPARATOR)

        tree_items = self._items_for(self.app.directory.tree)
        if tree_items:
            items.extend(tree_items)
        else:
            items.append(pystray.MenuItem("(no sessions yet)", None, enabled=False))
        items.append(pystray.Menu.SEPARATOR)

        items.append(pystray.MenuItem("Open PuTTY Directory", self._on_open, default=True))
        items.append(pystray.MenuItem("Add session...", self._on_add))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Exit", self._on_exit))
        return pystray.Menu(*items)

    def _items_for(self, nodes: list[Node]) -> list:
        """Map the node tree onto pystray menu items, folders as submenus."""
        items = []
        for node in nodes:
            if node.is_folder:
                children = self._items_for(node.children)
                if not children:
                    children = [pystray.MenuItem("(empty)", None, enabled=False)]
                items.append(pystray.MenuItem(node.name, pystray.Menu(*children)))
            else:
                items.append(
                    pystray.MenuItem(node.name, self._connect_action(node.id))
                )
        return items

    def _recent_items(self) -> list:
        items = []
        for node_id in self.app.directory.settings.get("recent", [])[:MAX_RECENT]:
            found = self.app.directory.locate(node_id)
            if found is None or found.node.is_folder:
                continue
            path = "/".join(ancestor.name for ancestor in found.ancestors)
            label = f"{found.node.name}  ({path})" if path else found.node.name
            items.append(pystray.MenuItem(label, self._connect_action(node_id)))
        return items

    # -- callbacks (called on the tray thread) ----------------------------

    def _connect_action(self, node_id: str):
        def action(_icon=None, _item=None):
            self.dispatch(lambda: self.app.connect_node(node_id))
        return action

    def _on_open(self, _icon=None, _item=None):
        self.dispatch(self.app.show_window)

    def _on_add(self, _icon=None, _item=None):
        self.dispatch(self.app.add_session_from_tray)

    def _on_exit(self, _icon=None, _item=None):
        self.dispatch(self.app.quit_app)
