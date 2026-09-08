"""Tree data model.

A directory is an ordered list of nodes. A node is either a ``session`` (a leaf
holding connection details) or a ``folder`` (which holds children). Folders may
nest arbitrarily deep.

Folders also carry connection fields. Any field left empty on a session is
inherited from its nearest ancestor that sets it, so a "Project X" folder can
define the user / port / key file once for everything inside it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Iterator, Sequence

FOLDER = "folder"
SESSION = "session"

#: Fields a child inherits from its ancestors when left empty.
INHERITABLE = (
    "user",
    "port",
    "password",
    "key_file",
    "protocol",
    "putty_session",
    "extra_args",
)

PROTOCOLS = ("ssh", "telnet", "rlogin", "raw")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Node:
    type: str
    name: str = ""
    id: str = field(default_factory=new_id)

    # Connection fields. Valid on sessions; on folders they act as defaults.
    host: str = ""
    user: str = ""
    port: str = ""
    password: str = ""
    key_file: str = ""
    protocol: str = ""
    putty_session: str = ""
    extra_args: str = ""
    notes: str = ""

    # Folder only.
    children: list["Node"] = field(default_factory=list)
    expanded: bool = True

    @property
    def is_folder(self) -> bool:
        return self.type == FOLDER

    def to_dict(self) -> dict:
        data: dict = {"id": self.id, "type": self.type, "name": self.name}
        for key in ("host", "user", "port", "password", "key_file",
                    "protocol", "putty_session", "extra_args", "notes"):
            value = getattr(self, key)
            if value:
                data[key] = value
        if self.is_folder:
            data["expanded"] = self.expanded
            data["children"] = [child.to_dict() for child in self.children]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Node":
        node = cls(
            type=data.get("type") or (FOLDER if "children" in data else SESSION),
            name=data.get("name", ""),
            id=data.get("id") or new_id(),
            host=data.get("host", ""),
            user=data.get("user", ""),
            port=str(data.get("port", "") or ""),
            password=data.get("password", ""),
            key_file=data.get("key_file", ""),
            protocol=data.get("protocol", ""),
            putty_session=data.get("putty_session", ""),
            extra_args=data.get("extra_args", ""),
            notes=data.get("notes", ""),
            expanded=bool(data.get("expanded", True)),
        )
        node.children = [cls.from_dict(child) for child in data.get("children", [])]
        return node

    def copy(self) -> "Node":
        """Deep copy with fresh ids, so a duplicate is a genuinely new node."""
        clone = Node.from_dict(self.to_dict())
        for node in walk(clone):
            node.id = new_id()
        return clone


def walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from walk(child)


@dataclass
class Located:
    """Where a node sits in the tree."""

    node: Node
    siblings: list[Node]
    index: int
    ancestors: list[Node]


class Directory:
    def __init__(self, tree: list[Node] | None = None, settings: dict | None = None):
        self.tree: list[Node] = tree or []
        self.settings: dict = settings or {}

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": 2,
            "settings": self.settings,
            "tree": [node.to_dict() for node in self.tree],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Directory":
        if not data:
            return cls()
        if "tree" in data:
            tree = [Node.from_dict(item) for item in data.get("tree", [])]
            return cls(tree, dict(data.get("settings", {})))
        # v1 format: a flat {"entries": [...]} list.
        tree = []
        for entry in data.get("entries", []):
            entry = dict(entry)
            entry["type"] = SESSION
            if not entry.get("name"):
                entry["name"] = entry.get("host", "Unnamed")
            tree.append(Node.from_dict(entry))
        return cls(tree, dict(data.get("settings", {})))

    # -- lookup -----------------------------------------------------------

    def locate(self, node_id: str) -> Located | None:
        def search(siblings: list[Node], ancestors: list[Node]) -> Located | None:
            for index, node in enumerate(siblings):
                if node.id == node_id:
                    return Located(node, siblings, index, ancestors)
                hit = search(node.children, ancestors + [node])
                if hit is not None:
                    return hit
            return None

        return search(self.tree, [])

    def get(self, node_id: str) -> Node | None:
        found = self.locate(node_id)
        return found.node if found else None

    def all_nodes(self) -> Iterator[Node]:
        for node in self.tree:
            yield from walk(node)

    def container_for(self, node_id: str | None) -> tuple[list[Node], list[Node]]:
        """Return the sibling list and ancestors a new node should be added to.

        Adding while a folder is selected puts the node inside it; adding while a
        session is selected puts it next to that session.
        """
        if not node_id:
            return self.tree, []
        found = self.locate(node_id)
        if found is None:
            return self.tree, []
        if found.node.is_folder:
            return found.node.children, found.ancestors + [found.node]
        return found.siblings, found.ancestors

    # -- mutation ---------------------------------------------------------

    def remove(self, node_id: str) -> Node | None:
        found = self.locate(node_id)
        if found is None:
            return None
        return found.siblings.pop(found.index)

    def is_ancestor_of(self, node_id: str, other_id: str) -> bool:
        node = self.get(node_id)
        if node is None:
            return False
        return any(child.id == other_id for child in walk(node) if child is not node)

    def move(self, node_id: str, target_id: str | None, inside: bool) -> bool:
        """Move a node next to ``target_id``, or into it when ``inside`` is set.

        A ``target_id`` of None moves the node to the end of the root list.
        Returns False when the move is impossible (e.g. a folder into itself).
        """
        if node_id == target_id or (target_id and self.is_ancestor_of(node_id, target_id)):
            return False

        found = self.locate(node_id)
        if found is None:
            return False

        if target_id is None:
            destination, index = self.tree, len(self.tree)
        else:
            target = self.locate(target_id)
            if target is None:
                return False
            if inside and target.node.is_folder:
                destination, index = target.node.children, len(target.node.children)
            else:
                destination, index = target.siblings, target.index + 1

        node = found.siblings.pop(found.index)
        # Removing the node first shifts every later index in the same list.
        if destination is found.siblings and found.index < index:
            index -= 1
        destination.insert(min(index, len(destination)), node)
        return True

    def shift(self, node_id: str, delta: int) -> bool:
        """Reorder a node within its current parent."""
        found = self.locate(node_id)
        if found is None:
            return False
        index = found.index + delta
        if not 0 <= index < len(found.siblings):
            return False
        found.siblings.insert(index, found.siblings.pop(found.index))
        return True

    def outdent(self, node_id: str) -> bool:
        """Move a node out of its parent folder, to just after that folder."""
        found = self.locate(node_id)
        if found is None or not found.ancestors:
            return False
        parent = found.ancestors[-1]
        node = found.siblings.pop(found.index)
        grandparent = self.locate(parent.id)
        assert grandparent is not None
        grandparent.siblings.insert(grandparent.index + 1, node)
        return True

    def indent(self, node_id: str) -> bool:
        """Move a node into the folder immediately above it."""
        found = self.locate(node_id)
        if found is None or found.index == 0:
            return False
        previous = found.siblings[found.index - 1]
        if not previous.is_folder:
            return False
        previous.children.append(found.siblings.pop(found.index))
        previous.expanded = True
        return True


def resolve(node: Node, ancestors: Sequence[Node]) -> dict:
    """Flatten a session and its ancestors into the values to connect with."""
    config = {key: "" for key in INHERITABLE}
    for current in list(ancestors) + [node]:
        for key in INHERITABLE:
            value = getattr(current, key)
            if value:
                config[key] = value
    config["host"] = node.host
    config["name"] = node.name
    config["protocol"] = config["protocol"] or "ssh"
    return config
