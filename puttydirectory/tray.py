"""Notification-area (system tray) icon, in the style of Pageant.

Right-click the icon for the whole session tree as nested submenus; click a
session to launch it. Left-click opens the manager window.

Two backends, chosen at run time by :func:`_pick_backend`:

``sni``
    Our own ``org.kde.StatusNotifierItem`` implementation (see :mod:`.sni`).
    This is what modern Linux desktops actually speak, and it is preferred
    there - pystray's XEmbed icon is blank and inert on Plasma. Needs Pillow
    and jeepney.
``pystray``
    Used on Windows and macOS, and as the Linux fallback for the older XEmbed
    trays that some lightweight desktops still run.

Both render the same backend-neutral :mod:`.traymenu` tree, so the menu is
built once here. When neither backend works the app still runs as a plain
window - ``is_available()`` reports which.
"""

from __future__ import annotations

import base64
import io
import os
import sys
import threading
import traceback
from typing import Callable

from . import sni, traymenu
from .model import Node
from .traymenu import Item

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


def _pick_backend() -> str | None:
    """Which tray backend to use, or None if there is no usable one.

    Linux prefers SNI. On Plasma, and on GNOME with the AppIndicator extension,
    StatusNotifierItem is the only protocol the panel really implements;
    pystray's XEmbed icon there is blank and does nothing when clicked.
    """
    if PIL_ERROR is not None:
        return None
    if sys.platform.startswith("linux") and sni.is_available():
        return "sni"
    _probe()
    return "pystray" if pystray is not None else None


def is_available() -> bool:
    return _pick_backend() is not None


def unavailable_reason() -> str:
    if PIL_ERROR is not None:
        return (f"{PIL_ERROR}\n\nInstall the tray dependencies:\n"
                f"  pip install pillow")
    _probe()
    error = _TRAY_ERROR
    if sys.platform.startswith("linux"):
        # On Linux both backends have to have failed to get here, so report
        # both reasons - the SNI one is usually the actionable half.
        return (
            f"No StatusNotifierItem tray: {sni.unavailable_reason()}\n\n"
            f"No XEmbed fallback either: {error}\n\n"
            "A tray needs a running graphical session with a D-Bus session bus:\n"
            "  - start it from inside your desktop, not over SSH or a console\n"
            "  - on GNOME, install the AppIndicator extension\n"
            "  - autostarting via ~/.config/autostart gets this right for you"
        )
    if error is None:
        return "The tray is available."
    if isinstance(error, ImportError):
        return (f"{error}\n\nInstall the tray dependencies:\n"
                f"  pip install pystray pillow")
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
    """Owns the tray icon and keeps its menu in step with the directory.

    Both backends run their own loop on a background thread, so every callback
    arrives off the GUI thread and is marshalled back onto it by ``dispatch``.
    """

    def __init__(self, app, dispatch: Callable[[Callable[[], None]], None]):
        self.app = app
        self.dispatch = dispatch
        self.backend = None       # sni.StatusNotifierTray
        self.icon = None          # pystray.Icon
        self.kind: str | None = None
        self.error: BaseException | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> bool:
        kind = _pick_backend()
        debug(f"starting tray: backend={kind or 'none'} "
              f"desktop={os.environ.get('XDG_CURRENT_DESKTOP', '?')} "
              f"session={os.environ.get('XDG_SESSION_TYPE', '?')}")
        if kind is None:
            debug(f"tray unavailable: {unavailable_reason()}")
            return False
        if kind == "sni":
            if self._start_sni():
                return True
            # No SNI host answered - a bare window manager, say. XEmbed may
            # still work there, so it is worth trying before giving up.
            debug("no StatusNotifierItem host answered; trying XEmbed")
            _probe()
            if pystray is not None and self._start_pystray():
                return True
            return False
        return self._start_pystray()

    def _start_sni(self) -> bool:
        try:
            backend = sni.StatusNotifierTray(
                "puttydirectory", self._title(),
                icon_factory=make_image,
                menu_factory=self.build_items,
            )
            if not backend.start():
                return False
        except Exception as error:
            debug("could not start the StatusNotifierItem tray", error)
            self.error = error
            return False
        self.backend = backend
        self.kind = "sni"
        return True

    def _start_pystray(self) -> bool:
        # pystray's X11 backend sets HAS_MENU = False ("Menus are not supported
        # on X"), so on that path the icon has no menu at all - not merely no
        # submenus. Worth saying out loud rather than presenting a dead icon,
        # though a click still runs the default action, so it stays usable.
        self.menu_supported = bool(getattr(pystray.Icon, "HAS_MENU", True))
        if not self.menu_supported:
            debug("this pystray backend supports no menu; the icon will only "
                  "respond to clicks. Install a StatusNotifierItem host "
                  "(Plasma, or GNOME with the AppIndicator extension) for menus.")
        try:
            self.icon = pystray.Icon(
                "puttydirectory",
                icon=make_image(),
                title=self._title(),
                menu=self._pystray_menu(),
            )
        except Exception as error:
            # Drawing the image or building the menu can fail on its own - most
            # often a Pillow codec missing from a frozen build.
            debug("could not create the tray icon", error)
            self.error = error
            self.icon = None
            return False
        self.kind = "pystray"
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
        if self.backend is not None:
            self.backend.stop()
            self.backend = None
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
        self.kind = None

    def _title(self) -> str:
        """The hover tooltip names the open file, so two directories are
        tellable apart from the tray alone."""
        store = getattr(self.app, "store", None)
        return (f"PuTTY Directory - {store.path.name}" if store
                else "PuTTY Directory - no directory open")

    def refresh(self) -> None:
        """Rebuild the menu after the directory changed."""
        debug(f"Tray.refresh() via {self.kind or 'no backend'}")
        if self.backend is not None:
            self.backend.refresh(title=self._title())
            return
        if self.icon is None:
            return
        try:
            self.icon.menu = self._pystray_menu()
            self.icon.update_menu()
            self.icon.title = self._title()
        except Exception as error:
            debug("could not update the pystray menu", error)

    def notify(self, message: str, title: str = "PuTTY Directory") -> None:
        if self.backend is not None:
            self.backend.notify(message, title)
            return
        if self.icon is None:
            return
        try:
            self.icon.notify(message, title)
        except Exception:
            pass  # Not every backend implements notifications.

    # -- menu (backend-neutral) -------------------------------------------

    def build_items(self) -> list[Item]:
        """The whole menu as :mod:`.traymenu` items, newest state each call."""
        items: list[Item] = []

        recent = self._recent_items()
        if recent:
            items.append(traymenu.submenu("Recent", recent))
            items.append(traymenu.separator())

        if self.app.store is None:
            items.append(traymenu.disabled("(no directory open)"))
        else:
            tree_items = self._items_for(self.app.directory.tree)
            items.extend(tree_items or [traymenu.disabled("(no sessions yet)")])
        items.append(traymenu.separator())

        items.append(traymenu.command("Open PuTTY Directory", self._on_open, default=True))
        items.append(traymenu.command("Add session...", self._on_add))
        items.append(traymenu.separator())
        items.append(traymenu.command("Exit", self._on_exit))
        return items

    def _items_for(self, nodes: list[Node]) -> list[Item]:
        """Map the node tree onto menu items, folders as submenus."""
        items: list[Item] = []
        for node in nodes:
            if node.is_folder:
                children = self._items_for(node.children)
                if not children:
                    children = [traymenu.disabled("(empty)")]
                items.append(traymenu.submenu(node.name, children))
            else:
                items.append(traymenu.command(node.name, self._connect_action(node.id)))
        return items

    def _recent_items(self) -> list[Item]:
        items: list[Item] = []
        for node_id in self.app.directory.settings.get("recent", [])[:MAX_RECENT]:
            found = self.app.directory.locate(node_id)
            if found is None or found.node.is_folder:
                continue
            path = "/".join(ancestor.name for ancestor in found.ancestors)
            label = f"{found.node.name}  ({path})" if path else found.node.name
            items.append(traymenu.command(label, self._connect_action(node_id)))
        return items

    # -- pystray rendering -------------------------------------------------

    def _pystray_menu(self):
        return pystray.Menu(*self._to_pystray(self.build_items()))

    def _to_pystray(self, items: list[Item]) -> list:
        out = []
        for item in items:
            if item.separator:
                out.append(pystray.Menu.SEPARATOR)
            elif item.is_submenu:
                children = self._to_pystray(item.children or [])
                if not children:
                    children = [pystray.MenuItem("(empty)", None, enabled=False)]
                out.append(pystray.MenuItem(item.label, pystray.Menu(*children)))
            else:
                out.append(pystray.MenuItem(
                    item.label,
                    self._wrap(item.action),
                    enabled=item.enabled and item.action is not None,
                    default=item.default,
                ))
        return out

    @staticmethod
    def _wrap(action):
        """pystray hands callbacks (icon, item); our items take no arguments."""
        if action is None:
            return None
        return lambda _icon=None, _item=None: action()

    # -- callbacks (called on the backend's thread) ------------------------

    def _connect_action(self, node_id: str):
        def action():
            self.dispatch(lambda: self.app.connect_node(node_id))
        return action

    def _on_open(self):
        self.dispatch(self.app.show_window)

    def _on_add(self):
        self.dispatch(self.app.add_session_from_tray)

    def _on_exit(self):
        self.dispatch(self.app.quit_app)
