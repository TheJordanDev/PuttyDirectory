"""The main window: a tree of folders and sessions, and a Connect button."""

from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import launcher, tray as tray_module
from .dialogs import NodeDialog, SettingsDialog
from .model import FOLDER, SESSION, Directory, Node, resolve, walk
from .store import Store, StoreError

DRAG_THRESHOLD = 6  # pixels before a click counts as a drag


class App:
    def __init__(self, root: tk.Tk, store: Store, directory: Directory,
                 start_hidden: bool = False):
        self.root = root
        self.store = store
        self.directory = directory
        self.dirty = False
        self.tray: tray_module.Tray | None = None

        self._drag_id: str | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._dragging = False

        root.title(f"PuTTY Directory - {store.path.name}")
        root.geometry("760x520")
        root.minsize(520, 320)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._set_window_icon()

        self._build_menu()
        self._build_toolbar()
        self._build_tree()
        self._build_statusbar()
        self._bind_keys()

        self._start_tray()
        self.refresh()

        if start_hidden and self.tray is not None:
            self.root.withdraw()

    def _set_window_icon(self) -> None:
        # Only needs Pillow, not a working tray - the window still gets its icon
        # on a machine where no tray backend can start.
        if not tray_module.can_draw_icon():
            return
        try:
            self._icon_image = tk.PhotoImage(data=tray_module.icon_photo_data())
            self.root.iconphoto(True, self._icon_image)
        except Exception:
            pass

    def _start_tray(self) -> None:
        if tray_module.is_available():
            tray = tray_module.Tray(self, dispatch=lambda fn: self.root.after(0, fn))
            if tray.start():
                self.tray = tray
        if self.tray is None:
            self.file_menu.entryconfigure("Hide to tray", state="disabled")

    # -- construction -----------------------------------------------------

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)

        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label=str(self.store.path), state="disabled")
        file_menu.add_separator()
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        file_menu.add_command(label="Open config folder", command=self.open_config_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Settings...", command=self.edit_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Hide to tray", command=self.hide_window)
        file_menu.add_command(label="Quit", command=self.quit_app)
        menu.add_cascade(label="File", menu=file_menu)
        self.file_menu = file_menu

        edit_menu = tk.Menu(menu, tearoff=0)
        edit_menu.add_command(label="New session", accelerator="Ctrl+N", command=self.add_session)
        edit_menu.add_command(label="New folder", accelerator="Ctrl+Shift+N", command=self.add_folder)
        edit_menu.add_separator()
        edit_menu.add_command(label="Edit", accelerator="F2", command=self.edit_selected)
        edit_menu.add_command(label="Duplicate", accelerator="Ctrl+D", command=self.duplicate_selected)
        edit_menu.add_command(label="Delete", accelerator="Del", command=self.delete_selected)
        edit_menu.add_separator()
        edit_menu.add_command(label="Move up", accelerator="Ctrl+Up", command=lambda: self.move_selected(-1))
        edit_menu.add_command(label="Move down", accelerator="Ctrl+Down", command=lambda: self.move_selected(1))
        edit_menu.add_command(label="Move into folder above", accelerator="Ctrl+Right", command=self.indent_selected)
        edit_menu.add_command(label="Move out of folder", accelerator="Ctrl+Left", command=self.outdent_selected)
        menu.add_cascade(label="Edit", menu=edit_menu)

        session_menu = tk.Menu(menu, tearoff=0)
        session_menu.add_command(label="Connect", accelerator="Enter", command=self.connect_selected)
        session_menu.add_command(label="Show command line", command=self.show_command)
        menu.add_cascade(label="Session", menu=session_menu)

        self.root.config(menu=menu)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(6, 6))
        bar.pack(fill="x")

        ttk.Button(bar, text="Connect", command=self.connect_selected).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="+ Session", command=self.add_session).pack(side="left")
        ttk.Button(bar, text="+ Folder", command=self.add_folder).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Edit", command=self.edit_selected).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Delete", command=self.delete_selected).pack(side="left", padx=(4, 0))

        ttk.Label(bar, text="Filter:").pack(side="left", padx=(12, 4))
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.refresh(keep_selection=True))
        filter_entry = ttk.Entry(bar, textvariable=self.filter_var, width=18)
        filter_entry.pack(side="left")
        filter_entry.bind("<Escape>", lambda _event: self.filter_var.set(""))
        self.filter_entry = filter_entry

    def _build_tree(self) -> None:
        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=6)

        columns = ("host", "user", "port", "notes")
        self.tree = ttk.Treeview(frame, columns=columns, selectmode="browse")
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.heading("host", text="Host", anchor="w")
        self.tree.heading("user", text="User", anchor="w")
        self.tree.heading("port", text="Port", anchor="w")
        self.tree.heading("notes", text="Notes", anchor="w")
        self.tree.column("#0", width=240, stretch=True)
        self.tree.column("host", width=180, stretch=True)
        self.tree.column("user", width=110, stretch=False)
        self.tree.column("port", width=55, stretch=False, anchor="e")
        self.tree.column("notes", width=160, stretch=True)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        bold = ("TkDefaultFont", 9, "bold")
        self.tree.tag_configure(FOLDER, font=bold)
        self.tree.tag_configure("inherited", foreground="#7a7a7a")
        self.tree.tag_configure("drop", background="#cfe4ff")

        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<ButtonPress-1>", self._on_press)
        self.tree.bind("<B1-Motion>", self._on_motion)
        self.tree.bind("<ButtonRelease-1>", self._on_release)
        self.tree.bind("<<TreeviewOpen>>", lambda _e: self._record_open_state(True))
        self.tree.bind("<<TreeviewClose>>", lambda _e: self._record_open_state(False))

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Connect", command=self.connect_selected)
        self.menu.add_separator()
        self.menu.add_command(label="Edit", command=self.edit_selected)
        self.menu.add_command(label="Duplicate", command=self.duplicate_selected)
        self.menu.add_command(label="Delete", command=self.delete_selected)
        self.menu.add_separator()
        self.menu.add_command(label="New session here", command=self.add_session)
        self.menu.add_command(label="New folder here", command=self.add_folder)
        self.menu.add_separator()
        self.menu.add_command(label="Show command line", command=self.show_command)

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(8, 4))
        bar.pack(fill="x")
        self.status = tk.StringVar()
        ttk.Label(bar, textvariable=self.status, foreground="#555555").pack(side="left")
        self.putty_status = tk.StringVar()
        ttk.Label(bar, textvariable=self.putty_status, foreground="#555555").pack(side="right")

    def _bind_keys(self) -> None:
        binds = {
            "<Control-n>": lambda e: self.add_session(),
            "<Control-N>": lambda e: self.add_folder(),
            "<Control-s>": lambda e: self.save(),
            "<Control-d>": lambda e: self.duplicate_selected(),
            "<Control-f>": lambda e: self.filter_entry.focus_set(),
            "<F2>": lambda e: self.edit_selected(),
            "<Control-Up>": lambda e: self.move_selected(-1),
            "<Control-Down>": lambda e: self.move_selected(1),
            "<Control-Right>": lambda e: self.indent_selected(),
            "<Control-Left>": lambda e: self.outdent_selected(),
        }
        # Bound on the main window rather than bind_all, so that these do not
        # fire while a modal dialog has focus.
        for sequence, handler in binds.items():
            self.root.bind(sequence, lambda e, h=handler: (h(e), "break")[1])

        # Delete and Return only apply while the tree has focus, so they do not
        # interfere with typing in the filter box.
        self.tree.bind("<Delete>", lambda e: self.delete_selected())
        self.tree.bind("<Return>", lambda e: self.connect_selected())

    # -- tree rendering ---------------------------------------------------

    def refresh(self, keep_selection: bool = True, select: str | None = None) -> None:
        previous = select or (self.selected_id() if keep_selection else None)
        self.tree.delete(*self.tree.get_children())

        query = self.filter_var.get().strip().lower()
        self._insert_nodes("", self.directory.tree, [], query)

        if previous and self.tree.exists(previous):
            self.tree.selection_set(previous)
            self.tree.focus(previous)
            self.tree.see(previous)

        nodes = list(self.directory.all_nodes())
        sessions = sum(1 for node in nodes if not node.is_folder)
        folders = len(nodes) - sessions
        self.status.set(f"{sessions} sessions, {folders} folders")
        putty = launcher.find_putty(self.directory.settings.get("putty_path", ""))
        state = f"putty: {Path(putty).name}" if putty else "putty: NOT FOUND"
        self.putty_status.set(state if self.tray is None else f"{state}  |  tray active")

        if self.tray is not None:
            self.tray.refresh()

    def _insert_nodes(self, parent: str, nodes: list[Node], ancestors: list[Node], query: str) -> None:
        for node in nodes:
            if query and not self._subtree_matches(node, query):
                continue
            config = resolve(node, ancestors) if not node.is_folder else {}
            if node.is_folder:
                values = ("", "", "", node.notes.replace("\n", " "))
                tags = (FOLDER,)
            else:
                inherited = any(
                    not getattr(node, key) and config.get(key)
                    for key in ("user", "port")
                )
                values = (
                    node.host,
                    config.get("user", ""),
                    config.get("port", ""),
                    node.notes.replace("\n", " "),
                )
                tags = (SESSION, "inherited") if inherited else (SESSION,)

            self.tree.insert(
                parent, "end", iid=node.id, text=node.name, values=values, tags=tags,
                open=node.expanded or bool(query),
            )
            if node.children:
                self._insert_nodes(node.id, node.children, ancestors + [node], query)

    @staticmethod
    def _node_matches(node: Node, query: str) -> bool:
        haystack = " ".join((node.name, node.host, node.user, node.notes)).lower()
        return query in haystack

    def _subtree_matches(self, node: Node, query: str) -> bool:
        if self._node_matches(node, query):
            return True
        return any(self._subtree_matches(child, query) for child in node.children)

    def _record_open_state(self, is_open: bool) -> None:
        node = self.directory.get(self.tree.focus())
        if node is not None and node.is_folder:
            node.expanded = is_open
            self.mark_dirty()

    # -- selection helpers ------------------------------------------------

    def selected_id(self) -> str | None:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def selected_node(self) -> Node | None:
        node_id = self.selected_id()
        return self.directory.get(node_id) if node_id else None

    def mark_dirty(self) -> None:
        self.dirty = True

    # -- CRUD -------------------------------------------------------------

    def add_session(self) -> None:
        self._add_node(SESSION)

    def add_folder(self) -> None:
        self._add_node(FOLDER)

    def _add_node(self, node_type: str) -> None:
        siblings, ancestors = self.directory.container_for(self.selected_id())
        node = Node(type=node_type)
        inherited = self._inherited_values(ancestors)
        if NodeDialog(self.root, node, inherited).show() is None:
            return
        siblings.append(node)
        for ancestor in ancestors:
            ancestor.expanded = True
        self.save()
        self.refresh(select=node.id)

    def edit_selected(self) -> None:
        node_id = self.selected_id()
        if not node_id:
            return
        found = self.directory.locate(node_id)
        if found is None:
            return
        inherited = self._inherited_values(found.ancestors)
        if NodeDialog(self.root, found.node, inherited).show() is None:
            return
        self.save()
        self.refresh(select=node_id)

    def duplicate_selected(self) -> None:
        node_id = self.selected_id()
        found = self.directory.locate(node_id) if node_id else None
        if found is None:
            return
        clone = found.node.copy()
        clone.name = f"{clone.name} (copy)"
        found.siblings.insert(found.index + 1, clone)
        self.save()
        self.refresh(select=clone.id)

    def delete_selected(self) -> None:
        node = self.selected_node()
        if node is None:
            return
        if self.directory.settings.get("confirm_delete", True):
            detail = ""
            if node.is_folder and node.children:
                inner = sum(1 for _ in walk(node)) - 1
                detail = f"\n\nIt contains {inner} item(s), which will also be deleted."
            if not messagebox.askyesno("Delete", f"Delete {node.name!r}?{detail}", parent=self.root):
                return
        self.directory.remove(node.id)
        self.save()
        self.refresh(keep_selection=False)

    def _inherited_values(self, ancestors: list[Node]) -> dict:
        """What a node placed under ``ancestors`` would inherit."""
        if not ancestors:
            return {}
        return resolve(ancestors[-1], ancestors[:-1])

    # -- reordering -------------------------------------------------------

    def move_selected(self, delta: int) -> None:
        node_id = self.selected_id()
        if node_id and self.directory.shift(node_id, delta):
            self.save()
            self.refresh(select=node_id)

    def indent_selected(self) -> None:
        node_id = self.selected_id()
        if node_id and self.directory.indent(node_id):
            self.save()
            self.refresh(select=node_id)

    def outdent_selected(self) -> None:
        node_id = self.selected_id()
        if node_id and self.directory.outdent(node_id):
            self.save()
            self.refresh(select=node_id)

    # -- drag and drop ----------------------------------------------------

    def _on_press(self, event: tk.Event) -> None:
        self._drag_id = self.tree.identify_row(event.y)
        self._drag_origin = (event.x, event.y)
        self._dragging = False

    def _on_motion(self, event: tk.Event) -> None:
        if not self._drag_id or self._drag_origin is None or self.filter_var.get().strip():
            return
        if not self._dragging:
            dx = abs(event.x - self._drag_origin[0])
            dy = abs(event.y - self._drag_origin[1])
            if max(dx, dy) < DRAG_THRESHOLD:
                return
            self._dragging = True
            self.tree.configure(cursor="hand2")

        self._clear_drop_marker()
        target = self.tree.identify_row(event.y)
        if target and target != self._drag_id and not self.directory.is_ancestor_of(self._drag_id, target):
            self.tree.item(target, tags=self.tree.item(target, "tags") + ("drop",))

    def _on_release(self, event: tk.Event) -> None:
        self.tree.configure(cursor="")
        self._clear_drop_marker()
        source, dragging = self._drag_id, self._dragging
        self._drag_id, self._drag_origin, self._dragging = None, None, False
        if not dragging or not source:
            return

        target = self.tree.identify_row(event.y)
        if target == source:
            return

        target_node = self.directory.get(target) if target else None
        inside = bool(target_node and target_node.is_folder)
        if self.directory.move(source, target or None, inside):
            if target_node is not None and inside:
                target_node.expanded = True
            self.save()
            self.refresh(select=source)

    def _clear_drop_marker(self) -> None:
        for item_id in self.tree.tag_has("drop"):
            tags = tuple(tag for tag in self.tree.item(item_id, "tags") if tag != "drop")
            self.tree.item(item_id, tags=tags)

    # -- events -----------------------------------------------------------

    def _on_double_click(self, event: tk.Event) -> str | None:
        if self.tree.identify_region(event.x, event.y) not in ("tree", "cell"):
            return None
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return None
        node = self.directory.get(item_id)
        if node is None:
            return None
        if node.is_folder:
            self.tree.item(item_id, open=not self.tree.item(item_id, "open"))
            node.expanded = not node.expanded
            self.mark_dirty()
            return "break"
        self.connect_selected()
        return "break"

    def _on_right_click(self, event: tk.Event) -> None:
        item_id = self.tree.identify_row(event.y)
        if item_id:
            self.tree.selection_set(item_id)
            self.tree.focus(item_id)
        node = self.directory.get(item_id) if item_id else None
        state = "normal" if node is not None and not node.is_folder else "disabled"
        self.menu.entryconfigure("Connect", state=state)
        self.menu.entryconfigure("Show command line", state=state)
        for label in ("Edit", "Duplicate", "Delete"):
            self.menu.entryconfigure(label, state="normal" if node is not None else "disabled")
        self.menu.tk_popup(event.x_root, event.y_root)

    # -- connecting -------------------------------------------------------

    def _command_for(self, node_id: str | None) -> list[str] | None:
        """Resolve a node to a PuTTY command line, reporting problems as dialogs."""
        found = self.directory.locate(node_id) if node_id else None
        if found is None:
            return None
        if found.node.is_folder:
            messagebox.showinfo("Connect", "Select a session, not a folder.", parent=self.root)
            return None

        putty = launcher.find_putty(self.directory.settings.get("putty_path", ""))
        if not putty:
            messagebox.showerror(
                "PuTTY not found",
                "Could not find the putty executable.\n\n"
                "Install PuTTY, or set its path in File > Settings.",
                parent=self.root,
            )
            return None

        try:
            return launcher.build_command(putty, resolve(found.node, found.ancestors))
        except launcher.LaunchError as error:
            messagebox.showerror("Connect", str(error), parent=self.root)
            return None

    def _command_for_selection(self) -> list[str] | None:
        return self._command_for(self.selected_id())

    def connect_node(self, node_id: str) -> None:
        """Launch a session by id. Shared by the window and the tray menu."""
        command = self._command_for(node_id)
        if command is None:
            return
        try:
            launcher.launch(command)
        except launcher.LaunchError as error:
            messagebox.showerror("Connect", str(error), parent=self.root)
            return

        node = self.directory.get(node_id)
        name = node.name if node else "session"
        self.status.set(f"Launched {name}")
        self._record_recent(node_id)
        if self.tray is not None and not self.root.winfo_viewable():
            self.tray.notify(f"Launched {name}")

    def connect_selected(self) -> None:
        node_id = self.selected_id()
        if node_id:
            self.connect_node(node_id)

    def _record_recent(self, node_id: str) -> None:
        """Keep a most-recently-connected list for the top of the tray menu."""
        recent = [item for item in self.directory.settings.get("recent", []) if item != node_id]
        recent.insert(0, node_id)
        self.directory.settings["recent"] = recent[:tray_module.MAX_RECENT]
        self.save()
        if self.tray is not None:
            self.tray.refresh()

    def show_command(self) -> None:
        command = self._command_for_selection()
        if command is None:
            return
        text = launcher.redact(command)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        messagebox.showinfo(
            "Command line",
            f"{text}\n\n(Copied to clipboard, with the password masked.)",
            parent=self.root,
        )

    # -- settings and persistence -----------------------------------------

    def edit_settings(self) -> None:
        detected = launcher.find_putty(self.directory.settings.get("putty_path", ""))
        updated = SettingsDialog(self.root, dict(self.directory.settings), detected).show()
        if updated is None:
            return
        self.directory.settings = updated
        self.save()
        self.refresh()

    def open_config_folder(self) -> None:
        folder = self.store.path.parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            import subprocess
            import sys

            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(folder)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as error:
            messagebox.showerror("Open folder", str(error), parent=self.root)

    def save(self) -> None:
        try:
            self.store.save(self.directory)
            self.dirty = False
        except StoreError as error:
            messagebox.showerror("Save failed", str(error), parent=self.root)

    # -- window / tray lifecycle -------------------------------------------

    def show_window(self) -> None:
        """Bring the manager window back from the tray."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.refresh()

    def hide_window(self) -> None:
        if self.tray is None:
            return
        if self.dirty:
            self.save()
        self.root.withdraw()
        if not self.directory.settings.get("tray_hint_shown"):
            self.directory.settings["tray_hint_shown"] = True
            self.save()
            self.tray.notify(
                "Still running here. Right-click for your sessions."
            )

    def add_session_from_tray(self) -> None:
        """Tray shortcut: show the window, then open a blank session dialog."""
        self.show_window()
        self.add_session()

    def on_close(self) -> None:
        """The window's X button: hide to the tray if there is one."""
        if self.tray is not None and self.directory.settings.get("close_to_tray", True):
            self.hide_window()
            return
        self.quit_app()

    def quit_app(self) -> None:
        if self.dirty:
            self.save()
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
        self.root.quit()
        self.root.destroy()


def _new_root() -> tk.Tk | None:
    """Create the Tk root, or explain why there is no display to put it on."""
    try:
        return tk.Tk()
    except tk.TclError as error:
        display = os.environ.get("DISPLAY")
        where = f"DISPLAY={display!r}" if display is not None else "DISPLAY is not set"
        print(
            f"Cannot open a window: {error}\n"
            f"({where})\n\n"
            "This is a graphical app, so it needs a desktop session. Over SSH or\n"
            "on a console there is nothing to draw on. Either run it from inside\n"
            "your desktop, or use the command line instead:\n"
            "  puttydirectory --list\n"
            '  puttydirectory --connect "Folder/Name"',
            file=sys.stderr,
        )
        return None


def run(config_path: Path, start_hidden: bool = False) -> int:
    store = Store(config_path)
    root = _new_root()
    if root is None:
        return 1

    try:
        directory = store.load()
    except StoreError as error:
        root.withdraw()
        messagebox.showerror("PuTTY Directory", str(error))
        root.destroy()
        return 1

    try:
        ttk.Style().theme_use("vista" if root.tk.call("tk", "windowingsystem") == "win32" else "clam")
    except tk.TclError:
        pass

    app = App(root, store, directory, start_hidden=start_hidden)
    if start_hidden and app.tray is None:
        messagebox.showwarning(
            "Tray unavailable",
            "Could not start a tray icon, so the window is shown instead.\n\n"
            + tray_module.unavailable_reason(),
            parent=root,
        )
    root.mainloop()
    return 0
