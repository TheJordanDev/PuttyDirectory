"""A backend-neutral description of the tray menu.

``tray.py`` builds one of these trees from the session directory; each backend
renders it in its own idiom - pystray menu objects on Windows, a D-Bus
``com.canonical.dbusmenu`` layout on Linux. Keeping the tree free of any
backend's types is what lets the two share the menu-building logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Item:
    """One row of the tray menu.

    An item is exactly one of three things: a separator, a submenu (it has
    ``children``), or a command (it has an ``action``). A command with no
    action is a disabled label, which is how empty states are drawn.
    """

    label: str = ""
    action: Optional[Callable[[], None]] = None
    children: Optional[list["Item"]] = None
    enabled: bool = True
    #: The action a plain left-click on the icon should run. Exactly one item
    #: in a tree should set this.
    default: bool = False
    separator: bool = False

    @property
    def is_submenu(self) -> bool:
        return self.children is not None


def separator() -> Item:
    return Item(separator=True)


def command(label: str, action: Callable[[], None], *, default: bool = False) -> Item:
    return Item(label=label, action=action, default=default)


def submenu(label: str, children: list[Item]) -> Item:
    return Item(label=label, children=children)


def disabled(label: str) -> Item:
    return Item(label=label, enabled=False)
