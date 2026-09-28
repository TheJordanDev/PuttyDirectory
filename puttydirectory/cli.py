"""PuTTY Directory - a standalone session manager that launches PuTTY.

    python main.py                          # default per-user directory file
    python main.py --config work.json       # a separate set of sessions
    python main.py --list                   # print the tree, no GUI
    python main.py --connect "Project/web1" # launch a session by path
    python main.py --tray                   # start minimised to the tray
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, launcher
from .model import Node, resolve
from .store import Store, StoreError, resolve_config_path


def attach_console() -> None:
    """Let a windowed frozen build print to the terminal that started it.

    The packaged executable is built with no console so that double-clicking it
    does not flash a black window. That would leave --list and --connect writing
    to nothing, so when arguments are present we attach to the parent terminal's
    console (if there is one) and reopen the standard streams onto it.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return

    import ctypes

    attach_parent_process = -1
    if not ctypes.windll.kernel32.AttachConsole(attach_parent_process):
        return  # Launched from Explorer, or the parent has no console.

    for name in ("stdout", "stderr"):
        try:
            setattr(sys, name, open("CONOUT$", "w", encoding="utf-8", buffering=1))
        except OSError:
            pass
    try:
        sys.stdin = open("CONIN$", "r", encoding="utf-8")
    except OSError:
        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="puttydirectory", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="path to the directory JSON file")
    parser.add_argument("--list", action="store_true", help="print the session tree and exit")
    parser.add_argument("--connect", metavar="PATH",
                        help="launch a session by its 'Folder/Name' path and exit")
    parser.add_argument("--tray", action="store_true",
                        help="start minimised to the notification area")
    parser.add_argument("--selftest", action="store_true",
                        help="verify the icon and tray pipeline, then exit")
    parser.add_argument("--tray-dump", action="store_true",
                        help="report the tray backend and the menu it would serve")
    parser.add_argument("--ui-dump", action="store_true",
                        help="report Tk's keyboard wiring for this build")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def command_selftest() -> int:
    """Check the things a frozen build breaks silently.

    Trimming modules to shrink the executable is easy to get wrong: PyInstaller
    reports a successful build, the app starts, and only the icons quietly stop
    working. Pillow swallows plugin-import failures, and pystray swallows the
    resulting icon failure, so nothing surfaces. This exercises those paths for
    real and fails loudly, so `--selftest` after a build catches it.
    """
    import io

    failures = 0

    def check(label: str, action) -> None:
        nonlocal failures
        try:
            action()
        except Exception as error:
            failures += 1
            print(f"  FAIL  {label}: {type(error).__name__}: {error}")
        else:
            print(f"  ok    {label}")

    print(f"PuTTY Directory {__version__} self-test")
    print(f"  frozen: {bool(getattr(sys, 'frozen', False))}")

    from . import tray

    check("Pillow available", lambda: _assert(tray.can_draw_icon(), "Pillow did not import"))
    check("draw the icon", lambda: tray.make_image(64))
    # PNG backs the window icon; ICO is what pystray converts into the tray
    # HICON. Both need PIL.PngImagePlugin, which needs fractions -> decimal.
    check("save PNG (window icon)", lambda: tray.make_image(16).save(io.BytesIO(), format="PNG"))
    check("save ICO (tray icon)", lambda: tray.make_image(16).save(io.BytesIO(), format="ICO"))
    check("tkinter importable", lambda: __import__("tkinter"))

    # The tray backend is the one check that needs a display, so on a headless
    # Linux box (a CI runner, a build over SSH) it is skipped rather than
    # failed. The codec checks above are what actually catch a bad build.
    import os

    headless = sys.platform != "win32" and not os.environ.get("DISPLAY")
    if headless and not tray.is_available():
        print("  skip  pystray backend: no DISPLAY (headless build host)")
    else:
        check("pystray backend", lambda: _assert(tray.is_available(), tray.unavailable_reason()))

    if failures:
        print(f"\n{failures} check(s) failed - the build is broken.", file=sys.stderr)
        return 1
    print("\nAll checks passed.")
    return 0


def command_tray_dump(config_path) -> int:
    """Report what the tray would do here, and the exact menu it would serve.

    The tray depends on things that cannot be reproduced from another machine:
    which desktop is running, whether a StatusNotifierItem host answers, which
    pystray backend got picked. This prints all of it plus the D-Bus menu tree,
    so a "the menu does not work" report can be diagnosed from the output
    instead of guessed at.
    """
    import os

    from . import sni, tray

    print(f"PuTTY Directory {__version__} tray diagnostics")
    print(f"  platform            : {sys.platform}")
    for name in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "DISPLAY",
                 "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
        value = os.environ.get(name)
        print(f"  {name:<20}: {value if value else '(unset)'}")

    print()
    print(f"  Pillow available    : {tray.can_draw_icon()}")
    print(f"  SNI available       : {sni.is_available()}")
    if not sni.is_available():
        print(f"     why not          : {sni.unavailable_reason().splitlines()[0]}")

    tray._probe()
    if tray.pystray is None:
        print(f"  pystray             : unavailable")
    else:
        backend = getattr(tray.pystray.Icon, "__module__", "?")
        has_menu = getattr(tray.pystray.Icon, "HAS_MENU", True)
        print(f"  pystray backend     : {backend}")
        print(f"  ...supports menus   : {has_menu}"
              + ("" if has_menu else "   <-- this backend has NO menu at all"))

    chosen = tray._pick_backend()
    print(f"  backend that wins   : {chosen or 'none - window only'}")

    # The menu tree, rendered the way the D-Bus host would receive it.
    print()
    print("  menu the tray would serve:")
    items = _sample_menu(config_path)
    model = sni._MenuModel()
    model.rebuild(items)

    def show(ident: int, depth: int) -> None:
        for child in model.children.get(ident, []):
            item = model.items[child]
            label = "----" if item.separator else item.label
            marks = []
            if item.is_submenu:
                marks.append("submenu")
            if item.default:
                marks.append("default")
            if not item.enabled:
                marks.append("disabled")
            suffix = f"  [{', '.join(marks)}]" if marks else ""
            print(f"    {'    ' * depth}id={child:<4} {label}{suffix}")
            show(child, depth + 1)

    show(0, 0)
    print()
    print(f"  dbusmenu revision   : {model.revision}")
    print("  If submenus are missing in your panel but listed above, the menu is")
    print("  being built correctly and the problem is on the host/protocol side.")
    return 0


def command_ui_dump() -> int:
    """Report how Tk wires the keyboard here, and whether our fix took.

    Tk binds Ctrl+A differently per windowing system *and* per release: some
    builds fold it into <<LineStart>>, where it moves the caret instead of
    selecting. That cannot be reproduced from another machine, so this prints
    the actual tables rather than what the docs say they should be.

    Its mere presence also answers "is this build current?" - an older binary
    rejects --ui-dump outright.
    """
    import tkinter as tk

    from .app import App

    root = tk.Tk()
    root.withdraw()

    print(f"PuTTY Directory {__version__} UI diagnostics")
    print(f"  tk version          : {root.tk.call('info', 'patchlevel')}")
    print(f"  windowingsystem     : {root.tk.call('tk', 'windowingsystem')}")

    print("\n  virtual events Ctrl+A could belong to:")
    for name in ("<<SelectAll>>", "<<LineStart>>", "<<PrevChar>>", "<<SelectNone>>"):
        mapped = root.tk.call("event", "info", name)
        hit = "  <-- Ctrl+A is here" if "Control-Key-a" in str(mapped) else ""
        print(f"    {name:<16}: {mapped}{hit}")

    print("\n  class bindings for Ctrl+A, before our fix:")
    for klass in ("TEntry", "Entry", "Text", "TCombobox"):
        existing = root.bind_class(klass, "<Control-Key-a>")
        print(f"    {klass:<12}: {existing or '(none)'}")

    App._fix_text_field_keys(_Stub(root))

    print("\n  after our fix:")
    for klass in ("TEntry", "Entry", "Text", "TCombobox"):
        existing = root.bind_class(klass, "<Control-Key-a>")
        state = "bound" if existing else "STILL UNBOUND - fix did not apply"
        print(f"    {klass:<12}: {state}")

    print("\n  If Ctrl+A still moves the caret with all four bound, the binding")
    print("  is being overridden per-widget; send this output.")
    root.destroy()
    return 0


class _Stub:
    """Just enough of App for _fix_text_field_keys, which only needs root."""

    def __init__(self, root):
        self.root = root


def _sample_menu(path) -> list:
    """The real menu tree for the given directory, without needing a GUI."""
    try:
        directory = Store(path).load()
    except StoreError:
        directory = None

    from . import traymenu

    if directory is None:
        return [traymenu.disabled("(no directory open)")]

    def build(nodes):
        out = []
        for node in nodes:
            if node.is_folder:
                children = build(node.children) or [traymenu.disabled("(empty)")]
                out.append(traymenu.submenu(node.name, children))
            else:
                out.append(traymenu.command(node.name, lambda: None))
        return out

    items = build(directory.tree) or [traymenu.disabled("(no sessions yet)")]
    items.append(traymenu.separator())
    items.append(traymenu.command("Open PuTTY Directory", lambda: None, default=True))
    items.append(traymenu.command("Exit", lambda: None))
    return items


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def iter_paths(nodes: list[Node], ancestors: list[Node] | None = None):
    """Yield (path string, node, ancestors) for every node in the tree."""
    ancestors = ancestors or []
    for node in nodes:
        path = "/".join([ancestor.name for ancestor in ancestors] + [node.name])
        yield path, node, ancestors
        if node.children:
            yield from iter_paths(node.children, ancestors + [node])


def command_list(directory) -> int:
    for path, node, ancestors in iter_paths(directory.tree):
        depth = path.count("/")
        indent = "  " * depth
        if node.is_folder:
            print(f"{indent}{node.name}/")
        else:
            config = resolve(node, ancestors)
            target = config["host"] or f"(session {config['putty_session']})"
            user = f"{config['user']}@" if config["user"] else ""
            port = f":{config['port']}" if config["port"] else ""
            print(f"{indent}{node.name}  ->  {user}{target}{port}")
    return 0


def command_connect(directory, wanted: str) -> int:
    matches = [
        (path, node, ancestors)
        for path, node, ancestors in iter_paths(directory.tree)
        if not node.is_folder and (path == wanted or node.name == wanted)
    ]
    if not matches:
        print(f"No session matching {wanted!r}. Use --list to see them.", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"{wanted!r} is ambiguous:", file=sys.stderr)
        for path, _node, _ancestors in matches:
            print(f"  {path}", file=sys.stderr)
        return 1

    _path, node, ancestors = matches[0]
    putty = launcher.find_putty(directory.settings.get("putty_path", ""))
    if not putty:
        print("Could not find the putty executable.", file=sys.stderr)
        return 1
    try:
        command = launcher.build_command(putty, resolve(node, ancestors))
        launcher.launch(command)
    except launcher.LaunchError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Launched: {launcher.redact(command)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if len(sys.argv) > 1:
        attach_console()
    args = parse_args(argv)
    path = resolve_config_path(args.config)

    if args.selftest:
        return command_selftest()

    if args.tray_dump:
        return command_tray_dump(path)

    if args.ui_dump:
        return command_ui_dump()

    if args.list or args.connect:
        try:
            directory = Store(path).load()
        except StoreError as error:
            print(str(error), file=sys.stderr)
            return 1
        return command_list(directory) if args.list else command_connect(directory, args.connect)

    from .app import run

    return run(path, start_hidden=args.tray)
