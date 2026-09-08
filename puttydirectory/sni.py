"""A StatusNotifierItem tray icon, spoken directly over D-Bus.

Why this exists
---------------
Modern Linux desktops (KDE Plasma, and GNOME via the AppIndicator extension)
do not implement the old XEmbed system tray. They implement
``org.kde.StatusNotifierItem`` (SNI), a D-Bus protocol.

pystray's Linux backends cannot reach that cleanly for a frozen build:

* ``_xorg`` draws a legacy XEmbed icon. Plasma only sees it through
  ``xembedsniproxy``, which derives the icon by screenshotting the X window -
  under Xwayland that window is never really composited, so the icon comes out
  **blank** - and relays clicks as synthetic X events the backend ignores, so
  clicking does **nothing**. That is the failure this module was written to fix.
* ``_appindicator``/``_gtk`` do speak SNI, but only through PyGObject and its
  system typelibs, which are not pip-installable and do not survive PyInstaller
  reliably.

So we implement the protocol ourselves on ``jeepney``: pure Python, a plain pip
dependency, and nothing for PyInstaller to mis-bundle.

We serve two objects on our own bus name:

``/StatusNotifierItem``
    The icon: its artwork, tooltip, and the Activate/ContextMenu methods.
``/MenuBar``
    The right-click menu, as ``com.canonical.dbusmenu`` - the companion
    protocol SNI hosts use to render a menu natively.
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

from .traymenu import Item

try:  # pragma: no cover - depends on what is installed
    from jeepney import (DBusAddress, MessageType, new_error, new_method_call,
                         new_method_return, new_signal)
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection

    JEEPNEY_ERROR: Optional[Exception] = None
except ImportError as error:  # pragma: no cover
    JEEPNEY_ERROR = error

SNI_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
INTROSPECT_IFACE = "org.freedesktop.DBus.Introspectable"
WATCHER_NAME = "org.kde.StatusNotifierWatcher"

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

#: Sizes we publish artwork in. Hosts pick whichever fits their panel.
ICON_SIZES = (22, 32, 48, 64)


def is_available() -> bool:
    """Can we even try? A session bus is required, and so is jeepney."""
    if JEEPNEY_ERROR is not None:
        return False
    return bool(
        os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        or os.path.exists(f"/run/user/{os.getuid()}/bus")
    )


def unavailable_reason() -> str:
    if JEEPNEY_ERROR is not None:
        return f"{JEEPNEY_ERROR}\n\nInstall the tray extra:  pip install jeepney"
    return "No D-Bus session bus is available."


def _argb32(image) -> bytes:
    """Pack a Pillow image the way the SNI spec wants it.

    ``a(iiay)`` pixmaps are ARGB32 in network byte order - big-endian, so the
    channel order in memory is literally A, R, G, B. Pillow gives us RGBA, so
    this is a channel shuffle, done with slice assignment to stay out of a
    per-pixel Python loop.
    """
    rgba = image.convert("RGBA").tobytes()
    out = bytearray(len(rgba))
    out[0::4] = rgba[3::4]  # A
    out[1::4] = rgba[0::4]  # R
    out[2::4] = rgba[1::4]  # G
    out[3::4] = rgba[2::4]  # B
    return bytes(out)


class _MenuModel:
    """Flattens an ``Item`` tree into the id-addressed form dbusmenu needs.

    dbusmenu never sends us an item; it sends the integer id of one. So every
    rebuild re-walks the tree and rebuilds the id map. Id 0 is reserved for the
    root by the specification.
    """

    def __init__(self) -> None:
        self.items: dict[int, Item] = {}
        self.children: dict[int, list[int]] = {0: []}
        self.revision = 1
        self._signature = None

    def rebuild(self, roots: list[Item]) -> bool:
        """Load a new tree. Returns whether it differs from the last one."""
        items: dict[int, Item] = {}
        children: dict[int, list[int]] = {0: []}
        counter = [0]

        def walk(nodes: list[Item], parent: int) -> None:
            for node in nodes:
                counter[0] += 1
                ident = counter[0]
                items[ident] = node
                children[parent].append(ident)
                children[ident] = []
                if node.is_submenu:
                    walk(node.children or [], ident)

        walk(roots, 0)

        # Compare by shape and labels: the callables differ on every rebuild
        # (they are freshly made closures), so they cannot be part of the key.
        signature = tuple(
            (i, items[i].label, items[i].separator, items[i].enabled,
             items[i].is_submenu, tuple(children[i]))
            for i in sorted(items)
        )
        changed = signature != self._signature
        self._signature = signature
        self.items = items
        self.children = children
        if changed:
            self.revision += 1
        return changed

    def properties(self, ident: int) -> dict:
        """The dbusmenu property dict for one item, as ``a{sv}``."""
        if ident == 0:
            return {"children-display": ("s", "submenu")}
        item = self.items.get(ident)
        if item is None:
            return {}
        if item.separator:
            return {"type": ("s", "separator")}
        props = {
            # An underscore is dbusmenu's mnemonic marker, so a literal one in
            # a session name has to be doubled or it silently disappears.
            "label": ("s", item.label.replace("_", "__")),
            "enabled": ("b", bool(item.enabled and (item.action or item.is_submenu))),
            "visible": ("b", True),
        }
        if item.is_submenu:
            props["children-display"] = ("s", "submenu")
        return props

    def layout(self, ident: int, depth: int) -> tuple:
        """One node as ``(ia{sv}av)``, recursing until ``depth`` runs out."""
        kids = []
        if depth != 0:
            for child in self.children.get(ident, []):
                kids.append(("(ia{sv}av)", self.layout(child, depth - 1)))
        return (ident, self.properties(ident), kids)


class StatusNotifierTray:
    """An SNI tray icon backed by a background D-Bus thread.

    ``icon_factory`` and ``menu_factory`` are called to (re)produce the artwork
    and the menu tree. Item actions are invoked on the D-Bus thread, so they
    should hand work back to the GUI thread themselves.
    """

    def __init__(self, app_id: str, title: str,
                 icon_factory: Callable[[int], object],
                 menu_factory: Callable[[], list[Item]]) -> None:
        self.app_id = app_id
        self.title = title
        self.icon_factory = icon_factory
        self.menu_factory = menu_factory

        self._conn = None
        self._bus_name = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._menu = _MenuModel()
        self._pixmaps: list[tuple[int, int, bytes]] = []
        self._default_action: Optional[Callable[[], None]] = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        if not is_available():
            return False
        try:
            self._conn = open_dbus_connection(bus="SESSION")
            if not self._claim_name():
                self._close()
                return False

            self._render()
            self._watch_for_host_restart()
            if not self._register():
                self._close()
                return False
        except Exception:
            self._close()
            return False

        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, daemon=True, name="sni-tray")
        self._thread.start()
        return True

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _claim_name(self) -> bool:
        """Take an ``org.kde.StatusNotifierItem-<pid>-<n>`` name off the bus.

        The spec asks for a name of this shape and hosts key off it. The pid
        makes it unique between processes; the counter covers one process
        wanting several icons, and a stale name still being held.
        """
        for index in range(1, 8):
            candidate = f"org.kde.StatusNotifierItem-{os.getpid()}-{index}"
            reply = self._conn.send_and_get_reply(message_bus.RequestName(candidate, 0))
            # 1 = we are the primary owner, 4 = we already owned it.
            if reply.body[0] in (1, 4):
                self._bus_name = candidate
                return True
        return False

    def _watch_for_host_restart(self) -> None:
        """Re-register when the tray host comes back.

        A panel restart (plasmashell crashing, or the user reloading it) takes
        the watcher down with it. Without this the icon would stay gone for the
        rest of the session, which looks exactly like the bug this module
        replaced. NameOwnerChanged tells us when to announce ourselves again.
        """
        rule = ("type='signal',sender='org.freedesktop.DBus',"
                "interface='org.freedesktop.DBus',member='NameOwnerChanged',"
                f"arg0='{WATCHER_NAME}'")
        try:
            bus = DBusAddress("/org/freedesktop/DBus", bus_name="org.freedesktop.DBus",
                              interface="org.freedesktop.DBus")
            self._conn.send_and_get_reply(
                new_method_call(bus, "AddMatch", "s", (rule,)), timeout=5
            )
        except Exception:
            pass  # We simply will not notice a restart; the icon still works.

    def _register(self) -> bool:
        watcher = DBusAddress("/StatusNotifierWatcher", bus_name=WATCHER_NAME,
                              interface=WATCHER_NAME)
        reply = self._conn.send_and_get_reply(
            new_method_call(watcher, "RegisterStatusNotifierItem", "s", (self._bus_name,)),
            timeout=5,
        )
        return reply.header.message_type is not MessageType.error

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None
        self._close()

    # -- state -------------------------------------------------------------

    def _render(self) -> None:
        """Refresh the cached artwork and menu from the factories."""
        pixmaps = []
        for size in ICON_SIZES:
            try:
                image = self.icon_factory(size)
            except Exception:
                continue
            pixmaps.append((size, size, _argb32(image)))
        self._pixmaps = pixmaps

        roots = self.menu_factory()
        self._menu.rebuild(roots)
        self._default_action = self._find_default(roots)

    def _find_default(self, nodes: list[Item]) -> Optional[Callable[[], None]]:
        for node in nodes:
            if node.default and node.action:
                return node.action
            if node.is_submenu:
                found = self._find_default(node.children or [])
                if found:
                    return found
        return None

    def refresh(self, title: str | None = None) -> None:
        """Rebuild the menu and tell the host to re-read it."""
        if self._conn is None:
            return
        try:
            if title and title != self.title:
                self.title = title
                self._emit(ITEM_PATH, SNI_IFACE, "NewTitle", None, ())
                self._emit(ITEM_PATH, SNI_IFACE, "NewToolTip", None, ())
            roots = self.menu_factory()
            self._default_action = self._find_default(roots)
            if self._menu.rebuild(roots):
                self._emit(MENU_PATH, MENU_IFACE, "LayoutUpdated", "ui",
                           (self._menu.revision, 0))
        except Exception:
            pass

    def notify(self, message: str, title: str) -> None:
        """Desktop notification via the standard freedesktop service."""
        if self._conn is None:
            return
        try:
            address = DBusAddress("/org/freedesktop/Notifications",
                                  bus_name="org.freedesktop.Notifications",
                                  interface="org.freedesktop.Notifications")
            call = new_method_call(
                address, "Notify", "susssasa{sv}i",
                (self.app_id, 0, self.app_id, title, message, [], {}, 5000),
            )
            with self._send_lock:
                self._conn.send(call)
        except Exception:
            pass

    # -- the D-Bus thread --------------------------------------------------

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                message = self._conn.receive(timeout=0.5)
            except TimeoutError:
                continue
            except Exception:
                break  # Bus went away; the window keeps working without us.
            if message.header.message_type is MessageType.signal:
                self._handle_signal(message)
                continue
            if message.header.message_type is not MessageType.method_call:
                continue
            try:
                self._handle(message)
            except Exception:
                try:
                    self._reply(new_error(message, "org.freedesktop.DBus.Error.Failed"))
                except Exception:
                    break

    def _handle_signal(self, message) -> None:
        """The only signal we subscribe to: the tray host reappearing."""
        if message.header.fields.get(3) != "NameOwnerChanged":
            return
        try:
            name, _old_owner, new_owner = message.body
        except ValueError:
            return
        if name == WATCHER_NAME and new_owner:
            try:
                self._register()
            except Exception:
                pass

    def _reply(self, message) -> None:
        with self._send_lock:
            self._conn.send(message)

    def _emit(self, path: str, interface: str, member: str, signature, body) -> None:
        signal = new_signal(DBusAddress(path, interface=interface),
                            member, signature, body)
        with self._send_lock:
            self._conn.send(signal)

    def _handle(self, message) -> None:
        fields = message.header.fields
        path = fields.get(1)       # HeaderFields.path
        interface = fields.get(2)  # HeaderFields.interface
        member = fields.get(3)     # HeaderFields.member
        body = message.body

        if interface == INTROSPECT_IFACE and member == "Introspect":
            self._reply(new_method_return(message, "s", (_introspection(path),)))
            return

        if interface == PROPS_IFACE:
            self._handle_properties(message, path, member, body)
            return

        if path == ITEM_PATH and interface == SNI_IFACE:
            self._handle_item(message, member)
            return

        if path == MENU_PATH and interface == MENU_IFACE:
            self._handle_menu(message, member, body)
            return

        self._reply(new_error(message, "org.freedesktop.DBus.Error.UnknownMethod"))

    # -- org.freedesktop.DBus.Properties -----------------------------------

    def _properties_for(self, path: str) -> dict:
        if path == ITEM_PATH:
            return {
                "Category": ("s", "ApplicationStatus"),
                "Id": ("s", self.app_id),
                "Title": ("s", self.title),
                "Status": ("s", "Active"),
                "WindowId": ("i", 0),
                "IconName": ("s", ""),
                "IconPixmap": ("a(iiay)", self._pixmaps),
                "OverlayIconName": ("s", ""),
                "OverlayIconPixmap": ("a(iiay)", []),
                "AttentionIconName": ("s", ""),
                "AttentionIconPixmap": ("a(iiay)", []),
                "AttentionMovieName": ("s", ""),
                "ToolTip": ("(sa(iiay)ss)", ("", [], self.title, "")),
                # False so a plain left-click reaches Activate() instead of
                # just popping the menu; the menu stays on right-click.
                "ItemIsMenu": ("b", False),
                "Menu": ("o", MENU_PATH),
            }
        return {
            "Version": ("u", 3),
            "Status": ("s", "normal"),
            "TextDirection": ("s", "ltr"),
            "IconThemePath": ("as", []),
        }

    def _handle_properties(self, message, path, member, body) -> None:
        props = self._properties_for(path)
        if member == "GetAll":
            self._reply(new_method_return(message, "a{sv}", (props,)))
        elif member == "Get":
            name = body[1]
            if name in props:
                self._reply(new_method_return(message, "v", (props[name],)))
            else:
                self._reply(new_error(message, "org.freedesktop.DBus.Error.UnknownProperty"))
        elif member == "Set":
            self._reply(new_method_return(message))
        else:
            self._reply(new_error(message, "org.freedesktop.DBus.Error.UnknownMethod"))

    # -- org.kde.StatusNotifierItem ----------------------------------------

    def _handle_item(self, message, member) -> None:
        if member in ("Activate", "SecondaryActivate"):
            # Left-click (and middle-click): open the manager window.
            self._reply(new_method_return(message))
            action = self._default_action
            if action is not None:
                action()
        elif member in ("ContextMenu", "Scroll", "ProvideXdgActivationToken",
                        "XAyatanaSecondaryActivate"):
            # ContextMenu is a no-op for us: we publish a Menu object path, so
            # the host renders the menu itself rather than asking us to.
            self._reply(new_method_return(message))
        else:
            self._reply(new_error(message, "org.freedesktop.DBus.Error.UnknownMethod"))

    # -- com.canonical.dbusmenu --------------------------------------------

    def _handle_menu(self, message, member, body) -> None:
        menu = self._menu
        if member == "GetLayout":
            parent, depth, _names = body
            layout = menu.layout(parent, depth)
            self._reply(new_method_return(message, "u(ia{sv}av)", (menu.revision, layout)))

        elif member == "GetGroupProperties":
            ids, _names = body
            wanted = ids or [0, *sorted(menu.items)]
            result = [(i, menu.properties(i)) for i in wanted if i == 0 or i in menu.items]
            self._reply(new_method_return(message, "a(ia{sv})", (result,)))

        elif member == "GetProperty":
            ident, name = body
            value = menu.properties(ident).get(name, ("s", ""))
            self._reply(new_method_return(message, "v", (value,)))

        elif member == "Event":
            ident, event_id, _data, _timestamp = body
            self._reply(new_method_return(message))
            if event_id == "clicked":
                self._activate(ident)

        elif member == "EventGroup":
            events = body[0]
            self._reply(new_method_return(message, "ai", ([],)))
            for ident, event_id, _data, _timestamp in events:
                if event_id == "clicked":
                    self._activate(ident)

        elif member == "AboutToShow":
            # The host is about to draw the menu - resync it with the directory
            # so a session added in the window shows up without a restart.
            changed = self._resync()
            self._reply(new_method_return(message, "b", (changed,)))

        elif member == "AboutToShowGroup":
            changed = self._resync()
            self._reply(new_method_return(message, "aiai", ([], [0] if changed else [])))

        else:
            self._reply(new_error(message, "org.freedesktop.DBus.Error.UnknownMethod"))

    def _resync(self) -> bool:
        try:
            roots = self.menu_factory()
            self._default_action = self._find_default(roots)
            return self._menu.rebuild(roots)
        except Exception:
            return False

    def _activate(self, ident: int) -> None:
        item = self._menu.items.get(ident)
        if item is not None and item.action is not None:
            item.action()


def _introspection(path: str) -> str:
    """Minimal introspection XML; some hosts probe before talking to us."""
    if path == ITEM_PATH:
        body = f"""
  <interface name="{SNI_IFACE}">
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <signal name="NewIcon"/><signal name="NewTitle"/><signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>"""
    else:
        body = f"""
  <interface name="{MENU_IFACE}">
    <method name="GetLayout">
      <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
      <arg type="u" direction="out"/><arg type="(ia{{sv}}av)" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/><arg type="s" direction="in"/>
      <arg type="v" direction="in"/><arg type="u" direction="in"/>
    </method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
  </interface>"""
    return f"""<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="{INTROSPECT_IFACE}">
    <method name="Introspect"><arg type="s" direction="out"/></method>
  </interface>
  <interface name="{PROPS_IFACE}">
    <method name="Get"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
    <method name="GetAll"><arg type="s" direction="in"/><arg type="a{{sv}}" direction="out"/></method>
  </interface>{body}
</node>"""
