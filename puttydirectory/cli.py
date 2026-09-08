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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


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

    if args.list or args.connect:
        try:
            directory = Store(path).load()
        except StoreError as error:
            print(str(error), file=sys.stderr)
            return 1
        return command_list(directory) if args.list else command_connect(directory, args.connect)

    from .app import run

    return run(path, start_hidden=args.tray)
