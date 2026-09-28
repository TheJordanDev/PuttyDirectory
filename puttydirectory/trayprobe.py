"""End-to-end check of the tray menu over a real D-Bus session bus.

Why this exists
---------------
The menu-goes-stale bugs were all diagnosed from a log and fixed against a
simulated host. That proves the handler logic, but it cannot prove the
transport: jeepney, the session bus, and the reply marshalling only exist on
Linux. This runs the whole thing for real, on the machine that has the problem.

It serves a tray (without registering, so no icon appears in the panel), then
talks to it from a second D-Bus connection using the exact sequence KDE uses:

    GetLayout(0, 1)          the root only - KDE never asks for more
    GetLayout(sub, 1)        a submenu's contents, fetched lazily on first open
    ... the directory changes ...
    GetLayout(0, 1)          KDE re-reads only the root after a change
    AboutToShow(sub)         must answer True, or the stale submenu is kept

That last answer is the whole bug. Run ``--tray-probe`` to check it here.
"""

from __future__ import annotations

from . import sni, traymenu

MENU_PATH = sni.MENU_PATH
MENU_IFACE = sni.MENU_IFACE


class _Probe:
    """A minimal dbusmenu client, standing in for the panel."""

    def __init__(self, bus_name):
        from jeepney import DBusAddress
        from jeepney.io.blocking import open_dbus_connection

        self.address = DBusAddress(MENU_PATH, bus_name=bus_name,
                                   interface=MENU_IFACE)
        self.conn = open_dbus_connection(bus="SESSION")

    def call(self, member, signature, body):
        from jeepney import MessageType, new_method_call

        reply = self.conn.send_and_get_reply(
            new_method_call(self.address, member, signature, body), timeout=5
        )
        if reply.header.message_type is MessageType.error:
            raise RuntimeError(f"{member} returned {reply.header.fields.get(4)}")
        return reply.body

    def get_layout(self, parent, depth):
        revision, node = self.call("GetLayout", "iias", (parent, depth, []))
        return revision, node

    def about_to_show(self, ident):
        return self.call("AboutToShow", "i", (ident,))[0]

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


def _child_ids(node):
    """Pull the child ids out of a (ia{sv}av) layout node.

    The 'av' members arrive as variants; jeepney may hand them back as the
    bare value or as a (signature, value) pair depending on version, so both
    shapes are accepted rather than assumed.
    """
    ids = []
    for entry in node[2]:
        value = entry
        if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[0], str):
            value = entry[1]
        ids.append(value[0])
    return ids


def _labels(node):
    out = []
    for entry in node[2]:
        value = entry
        if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[0], str):
            value = entry[1]
        label = value[1].get("label")
        out.append(label[1] if isinstance(label, tuple) else label)
    return [text for text in out if text]


def run() -> int:
    """Serve a tray, drive it like KDE does, and report. 0 = healthy."""
    if not sni.is_available():
        print("No D-Bus session bus here, so there is nothing to probe.")
        print(f"  {sni.unavailable_reason()}")
        return 2

    # A menu we control, so the probe can change it mid-run.
    state = {"sessions": ["alpha", "beta"]}

    def menu_factory():
        children = [traymenu.command(name, lambda: None)
                    for name in state["sessions"]]
        return [
            traymenu.submenu("Folder", children or [traymenu.disabled("(empty)")]),
            traymenu.separator(),
            traymenu.command("Open PuTTY Directory", lambda: None, default=True),
        ]

    from .tray import make_image

    tray = sni.StatusNotifierTray("puttydirectory-probe", "probe",
                                  icon_factory=make_image,
                                  menu_factory=menu_factory)
    if not tray.start(register=False):
        print("Could not serve a tray on the session bus.")
        return 2

    failures = []

    def check(label, got, want):
        ok = got == want
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}: {got!r}")
        if not ok:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    probe = None
    try:
        probe = _Probe(tray._bus_name)
        print(f"probing {tray._bus_name} over the session bus\n")

        revision, root = probe.get_layout(0, 1)
        folder = _child_ids(root)[0]
        print(f"  root at revision {revision}, folder id {folder}")

        probe.about_to_show(folder)
        _revision, node = probe.get_layout(folder, 1)
        check("folder contents as first fetched", _labels(node), ["alpha", "beta"])

        print("\n  the directory changes (a session is removed):")
        state["sessions"] = ["alpha"]
        tray.refresh()

        # KDE re-reads only the root after a change.
        probe.get_layout(0, 1)

        check("AboutToShow(folder) reports it stale", probe.about_to_show(folder), True)

        _revision, node = probe.get_layout(folder, 1)
        check("folder contents after re-reading", _labels(node), ["alpha"])

        print("\n  and it settles (no refetch loop):")
        check("AboutToShow(folder) with nothing changed",
              probe.about_to_show(folder), False)
        check("AboutToShow(root) with nothing changed",
              probe.about_to_show(0), False)
    except Exception as error:
        print(f"  FAIL  the probe could not complete: "
              f"{type(error).__name__}: {error}")
        failures.append(str(error))
    finally:
        if probe is not None:
            probe.close()
        tray.stop()

    if failures:
        print(f"\n{len(failures)} failure(s) - the tray menu will go stale:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("\nThe tray menu updates correctly over D-Bus on this machine.")
    return 0
