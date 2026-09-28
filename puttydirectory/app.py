"""The main window: a tree of folders and sessions, and a Connect button."""

from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import launcher, scaling, tray as tray_module
from .dialogs import ImportPuttyDialog, NodeDialog, SettingsDialog
from .model import FOLDER, SESSION, Directory, Node, resolve, walk
from . import puttyimport
from .preferences import Preferences
from .store import Store, StoreError

DRAG_THRESHOLD = 6  # pixels before a click counts as a drag

#: Window and column sizes as designed at 100%; everything else is derived from
#: these by UiScale, so there is one place to change a default.
BASE_WINDOW = (760, 520)
BASE_MINSIZE = (520, 320)
BASE_COLUMNS = {"#0": 240, "host": 180, "user": 110, "port": 55, "notes": 160}

#: The tree draws folder rows in bold. It has to be a named font so the scaler
#: can resize it with everything else.
FOLDER_FONT = "PuttyDirectoryFolderFont"


class App:
    def __init__(self, root: tk.Tk, store: Store | None, directory: Directory,
                 start_hidden: bool = False, prefs: Preferences | None = None):
        self.root = root
        self.store = store
        self.directory = directory
        self.prefs = prefs or Preferences.load()
        self.dirty = False
        # Older directory files carried app-level settings; take them over once
        # so upgrading does not lose the configured PuTTY path.
        self.prefs.adopt_legacy(directory.settings)
        if store is not None:
            self.prefs.remember_file(store.path)
        self.tray: tray_module.Tray | None = None

        self._drag_id: str | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._dragging = False

        root.title("PuTTY Directory")

        # Scale before any widget is built, so the first layout is already the
        # right size rather than being resized out from under the user. The
        # factor is an app preference, not a per-directory one.
        self.style = ttk.Style()
        # The expander triangle is sized in raw pixels by the theme, so it needs
        # scaling like any other pixel measurement. Read the theme's own value
        # as the 100% baseline rather than guessing one.
        try:
            self._base_indicator = int(self.style.lookup("Treeview.Item", "indicatorsize") or 9)
        except (tk.TclError, ValueError):
            self._base_indicator = 9
        self.scale = scaling.UiScale(root)
        self.scale.apply(self.prefs.get("ui_scale", 1.0))
        self.scale_var = tk.DoubleVar(value=self.scale.factor)
        root.geometry(self.scale.geometry(*BASE_WINDOW))
        root.minsize(*self.scale.dims(*BASE_MINSIZE))

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._set_window_icon()

        self._build_menu()
        self._build_toolbar()
        self._build_tree()
        self._build_statusbar()
        self._bind_keys()

        self._start_tray()
        self._sync_file_state()
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
        except Exception as error:
            tray_module.debug("could not set the window icon", error)
            tray_module._diagnose_pillow()

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
        file_menu.add_command(label="New directory...", accelerator="Ctrl+Shift+O",
                              command=self.new_file)
        file_menu.add_command(label="Open...", accelerator="Ctrl+O", command=self.open_file_dialog)

        self.recent_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Open recent", menu=self.recent_menu)

        file_menu.add_command(label="Close", accelerator="Ctrl+W", command=self.close_file)
        file_menu.add_separator()
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save_now)
        file_menu.add_command(label="Save as...", accelerator="Ctrl+Shift+S", command=self.save_as)
        file_menu.add_command(label="Reveal in file manager", command=self.open_config_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Settings...", command=self.edit_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Hide to tray", command=self.hide_window)
        file_menu.add_command(label="Quit", command=self.quit_app)
        menu.add_cascade(label="File", menu=file_menu)
        self.file_menu = file_menu

        edit_menu = tk.Menu(menu, tearoff=0, postcommand=self._label_edit_entries)
        edit_menu.add_command(label="New session", accelerator="Ctrl+N", command=self.add_session)
        self._edit_new_session_index = edit_menu.index("end")
        edit_menu.add_command(label="New folder", accelerator="Ctrl+Shift+N", command=self.add_folder)
        self._edit_new_folder_index = edit_menu.index("end")
        edit_menu.add_command(label="Import from PuTTY...", command=self.import_from_putty)
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
        self.edit_menu = edit_menu

        session_menu = tk.Menu(menu, tearoff=0)
        session_menu.add_command(label="Connect", accelerator="Enter", command=self.connect_selected)
        session_menu.add_command(label="Show command line", command=self.show_command)
        menu.add_cascade(label="Session", menu=session_menu)

        view_menu = tk.Menu(menu, tearoff=0)
        view_menu.add_command(label="Larger", accelerator="Ctrl++",
                              command=lambda: self.nudge_scale(scaling.STEP))
        view_menu.add_command(label="Smaller", accelerator="Ctrl+-",
                              command=lambda: self.nudge_scale(-scaling.STEP))
        view_menu.add_command(label="Reset to 100%", accelerator="Ctrl+0",
                              command=lambda: self.set_scale(1.0))
        view_menu.add_separator()
        for preset in scaling.PRESETS:
            view_menu.add_radiobutton(
                label=f"{preset:.0%}", value=preset, variable=self.scale_var,
                command=lambda value=preset: self.set_scale(value),
            )
        menu.add_cascade(label="View", menu=view_menu)
        self.view_menu = view_menu

        self.root.config(menu=menu)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(6, 6))
        bar.pack(fill="x")

        connect = ttk.Button(bar, text="Connect", command=self.connect_selected)
        connect.pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        add_session = ttk.Button(bar, text="+ Session", command=self.add_session)
        add_session.pack(side="left")
        add_folder = ttk.Button(bar, text="+ Folder", command=self.add_folder)
        add_folder.pack(side="left", padx=(4, 0))
        edit = ttk.Button(bar, text="Edit", command=self.edit_selected)
        edit.pack(side="left", padx=(4, 0))
        delete = ttk.Button(bar, text="Delete", command=self.delete_selected)
        delete.pack(side="left", padx=(4, 0))
        # Everything here needs a directory open; _sync_file_state greys them out.
        self.file_buttons = [connect, add_session, add_folder, edit, delete]

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
        for column in BASE_COLUMNS:
            self.tree.column(column, stretch=column not in ("user", "port"),
                             **({"anchor": "e"} if column == "port" else {}))

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # Derived from the default font rather than hardcoded, so it follows both
        # the system font and the UI scale.
        self.tree.tag_configure(
            FOLDER, font=self.scale.derive(FOLDER_FONT, weight="bold")
        )
        self._apply_tree_metrics()
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
        # Indices are kept because _label_create_entries rewrites these labels
        # to name the destination, so they cannot be addressed by label.
        self.menu.add_command(label="New session", command=self.add_session)
        self._new_session_index = self.menu.index("end")
        self.menu.add_command(label="New folder", command=self.add_folder)
        self._new_folder_index = self.menu.index("end")
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
            "<Control-o>": lambda e: self.open_file_dialog(),
            "<Control-O>": lambda e: self.new_file(),
            "<Control-w>": lambda e: self.close_file(),
            "<Control-S>": lambda e: self.save_as(),
            "<Control-n>": lambda e: self.add_session(),
            "<Control-N>": lambda e: self.add_folder(),
            "<Control-s>": lambda e: self.save_now(),
            "<Control-d>": lambda e: self.duplicate_selected(),
            "<Control-f>": lambda e: self.filter_entry.focus_set(),
            "<F2>": lambda e: self.edit_selected(),
            "<Control-Up>": lambda e: self.move_selected(-1),
            "<Control-Down>": lambda e: self.move_selected(1),
            "<Control-Right>": lambda e: self.indent_selected(),
            "<Control-Left>": lambda e: self.outdent_selected(),
            # Both the shifted and unshifted key, and the numpad, so Ctrl+= works
            # the way it does in a browser.
            "<Control-plus>": lambda e: self.nudge_scale(scaling.STEP),
            "<Control-equal>": lambda e: self.nudge_scale(scaling.STEP),
            "<Control-KP_Add>": lambda e: self.nudge_scale(scaling.STEP),
            "<Control-minus>": lambda e: self.nudge_scale(-scaling.STEP),
            "<Control-KP_Subtract>": lambda e: self.nudge_scale(-scaling.STEP),
            "<Control-0>": lambda e: self.set_scale(1.0),
        }
        # These are also text-editing keys: Tk's Entry class binds Control-d to
        # "delete character", and Control-Left/Right are <<PrevWord>>/<<NextWord>>.
        # The class binding runs before this one, so without a guard typing
        # Ctrl+D in the filter box would delete a character *and* duplicate the
        # selected node. While a text field has focus they belong to the text.
        text_keys = {"<Control-d>", "<Control-Left>", "<Control-Right>",
                     "<Control-Up>", "<Control-Down>"}

        # Bound on the main window rather than bind_all, so that these do not
        # fire while a modal dialog has focus.
        for sequence, handler in binds.items():
            guard = sequence in text_keys
            self.root.bind(sequence, lambda e, h=handler, g=guard:
                           None if (g and self._typing()) else (h(e), "break")[1])

        # Delete and Return only apply while the tree has focus, so they do not
        # interfere with typing in the filter box.
        self.tree.bind("<Delete>", lambda e: self.delete_selected())
        self.tree.bind("<Return>", lambda e: self.connect_selected())
        # The keyboard route back to "nothing selected"; clicking empty space is
        # the other. Needed because a new node is created inside the selected
        # folder, so without it the top level becomes unreachable.
        self.tree.bind("<Escape>", lambda e: self.clear_selection())

        self._fix_text_field_keys()

    def _fix_text_field_keys(self) -> None:
        """Make Ctrl+A select all in every text field, on every platform.

        Tk wires this differently per windowing system, and differently between
        Tk releases. On win32 <<SelectAll>> covers Control-a. On x11 it does
        not, and depending on the Tk build Control-a is instead folded into
        <<LineStart>>, so Ctrl+A moves the caret to the start of the field -
        which is what it does on Linux here.

        Adding Control-a to <<SelectAll>> is not enough to correct that: when a
        key matches both a virtual and a physical pattern Tk prefers the
        physical one, and between two virtual patterns the result is not ours
        to control. Binding the concrete key on the widget classes wins
        outright, and applies to fields the dialogs create later. Done on all
        platforms so the behaviour cannot drift apart again.
        """
        for klass in ("TEntry", "Entry", "TCombobox", "Text"):
            self.root.bind_class(klass, "<Control-Key-a>", self._select_all_text)

    @staticmethod
    def _select_all_text(event) -> str:
        widget = event.widget
        try:
            if widget.winfo_class() == "Text":
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "1.0")
            else:
                widget.selection_range(0, "end")
                widget.icursor("end")
        except tk.TclError:
            pass
        return "break"

    def _typing(self) -> bool:
        """Is focus in a text field, where editing keys outrank accelerators?"""
        widget = self.root.focus_get()
        if widget is None:
            return False
        return widget.winfo_class() in ("Entry", "TEntry", "Text", "TCombobox")

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

        if not self.has_file():
            self.status.set("No directory open  -  File > Open, or File > New directory")
        else:
            nodes = list(self.directory.all_nodes())
            sessions = sum(1 for node in nodes if not node.is_folder)
            folders = len(nodes) - sessions
            self.status.set(f"{sessions} sessions, {folders} folders")
        putty = launcher.find_putty(self.prefs.get("putty_path", ""))
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
        if not self._require_file():
            return
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

    def import_from_putty(self) -> None:
        """Copy sessions out of PuTTY's own store into the open directory.

        Read-only towards PuTTY: nothing is removed or changed on its side.
        """
        if not self._require_file():
            return

        sessions = puttyimport.read_sessions()
        if not sessions:
            messagebox.showinfo(
                "Import from PuTTY",
                "No saved PuTTY sessions found.\n\nLooked in:\n"
                f"{puttyimport.where_it_looked()}\n\n"
                "Sessions you have only typed into PuTTY without saving are not "
                "stored anywhere, so there is nothing to import.",
                parent=self.root,
            )
            return

        siblings, ancestors = self.directory.container_for(self.selected_id())
        where = "/".join(node.name for node in ancestors) or self.store.path.name
        existing = {node.name for node in self.directory.all_nodes()}

        answer = ImportPuttyDialog(self.root, sessions, existing, where).show()
        if answer is None:
            return
        chosen, folder_name, keep_reference = answer

        destination = siblings
        if folder_name:
            folder = Node(type=FOLDER, name=self._unique_name(folder_name, siblings))
            siblings.append(folder)
            destination = folder.children

        first = None
        for session in chosen:
            node = session.to_node(keep_putty_session=keep_reference)
            node.name = self._unique_name(node.name, destination)
            destination.append(node)
            first = first or node

        for ancestor in ancestors:
            ancestor.expanded = True
        self.save()
        self.refresh(select=first.id if first else None)
        self.status.set(f"Imported {len(chosen)} session(s) from PuTTY")

    @staticmethod
    def _unique_name(name: str, siblings: list[Node]) -> str:
        """Avoid colliding with a sibling, the way a file manager would."""
        taken = {node.name for node in siblings}
        if name not in taken:
            return name
        index = 2
        while f"{name} ({index})" in taken:
            index += 1
        return f"{name} ({index})"

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
        if self.prefs.get("confirm_delete", True):
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
        # Clicking past the last row clears the selection. Without this there is
        # no way back to "nothing selected", and since a new node is created
        # inside the selected folder, selecting one folder would leave no way to
        # create anything at the top level again.
        #
        # Keyed on the region rather than on identify_row() returning nothing:
        # a click on a column heading or a separator also has no row, and must
        # not deselect. "nothing" is the empty area below the last item.
        if not self._drag_id and self._region_at(event) == "nothing":
            self.clear_selection()

    def _region_at(self, event: tk.Event) -> str:
        try:
            return self.tree.identify_region(event.x, event.y)
        except tk.TclError:
            return ""

    def clear_selection(self) -> None:
        """Deselect everything, so the next new node goes to the top level."""
        selection = self.tree.selection()
        if selection:
            self.tree.selection_remove(*selection)
        # The focus ring is separate from the selection and would otherwise stay
        # drawn on the row that was just deselected.
        self.tree.focus("")

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
        else:
            # Right-clicking empty space targets the top level, and says so.
            self.clear_selection()
        node = self.directory.get(item_id) if item_id else None
        state = "normal" if node is not None and not node.is_folder else "disabled"
        self.menu.entryconfigure("Connect", state=state)
        self.menu.entryconfigure("Show command line", state=state)
        for label in ("Edit", "Duplicate", "Delete"):
            self.menu.entryconfigure(label, state="normal" if node is not None else "disabled")
        self._label_create_entries()
        self.menu.tk_popup(event.x_root, event.y_root)

    def _create_target(self) -> str:
        """Where a new node would go, phrased for a menu label."""
        node = self.selected_node()
        if node is None:
            return "at top level"
        if node.is_folder:
            return f"in {node.name}"
        # A session is selected, so the new node lands beside it.
        found = self.directory.locate(node.id)
        parent = found.ancestors[-1] if found and found.ancestors else None
        return f"in {parent.name}" if parent else "at top level"

    def _label_create_entries(self) -> None:
        """Retitle the create entries to name their destination.

        Where a new node lands depends on the selection, which is easy to get
        wrong silently - especially since selecting a folder is sticky. Saying
        it on the menu entry makes it checkable before clicking.
        """
        target = self._create_target()
        for index, kind in ((self._new_session_index, "session"),
                            (self._new_folder_index, "folder")):
            self.menu.entryconfigure(index, label=f"New {kind} {target}")

    def _label_edit_entries(self) -> None:
        """Same for the Edit menu, refreshed each time it is opened."""
        if not self.has_file():
            return
        target = self._create_target()
        for index, kind in ((self._edit_new_session_index, "session"),
                            (self._edit_new_folder_index, "folder")):
            self.edit_menu.entryconfigure(index, label=f"New {kind} {target}")

    # -- connecting -------------------------------------------------------

    def _command_for(self, node_id: str | None) -> list[str] | None:
        """Resolve a node to a PuTTY command line, reporting problems as dialogs."""
        found = self.directory.locate(node_id) if node_id else None
        if found is None:
            return None
        if found.node.is_folder:
            messagebox.showinfo("Connect", "Select a session, not a folder.", parent=self.root)
            return None

        putty = launcher.find_putty(self.prefs.get("putty_path", ""))
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

    # -- open / close / save-as -------------------------------------------

    def has_file(self) -> bool:
        return self.store is not None

    def _load_into(self, path: Path) -> bool:
        """Point the window at a directory file. False if it could not be read."""
        store = Store(path)
        try:
            directory = store.load()
        except StoreError as error:
            messagebox.showerror("Open", str(error), parent=self.root)
            self.prefs.forget_file(path)
            self._rebuild_recent_menu()
            return False

        self.prefs.adopt_legacy(directory.settings)
        self.store = store
        self.directory = directory
        self.dirty = False
        self.filter_var.set("")
        self.prefs.remember_file(path)
        self._sync_file_state()
        self.refresh(keep_selection=False)
        return True

    def _sync_file_state(self) -> None:
        """Update everything that depends on which file is open."""
        name = self.store.path.name if self.store else "no file open"
        self.root.title(f"PuTTY Directory - {name}")
        self._rebuild_recent_menu()

        state = "normal" if self.has_file() else "disabled"
        for label in ("Close", "Save", "Save as...", "Reveal in file manager"):
            self.file_menu.entryconfigure(label, state=state)
        for button in self.file_buttons:
            button.configure(state=state)

    def _rebuild_recent_menu(self) -> None:
        self.recent_menu.delete(0, "end")
        current = str(self.store.path.resolve()) if self.store else None
        entries = [item for item in self.prefs.recent_files if item != current]
        if not entries:
            self.recent_menu.add_command(label="(nothing yet)", state="disabled")
            return
        for item in entries:
            path = Path(item)
            # Show the parent folder too: several files are likely to be called
            # something like directory.json.
            label = f"{path.name}   -   {path.parent}"
            self.recent_menu.add_command(
                label=label, command=lambda p=path: self.open_path(p)
            )
        self.recent_menu.add_separator()
        self.recent_menu.add_command(label="Clear list", command=self._clear_recent)

    def _clear_recent(self) -> None:
        self.prefs.clear_recent_files()
        self._rebuild_recent_menu()

    def open_path(self, path: Path) -> None:
        if self.store and Path(path).resolve() == self.store.path.resolve():
            return
        if not Path(path).exists():
            if messagebox.askyesno(
                "Open",
                f"{path}\n\nThis file no longer exists. Remove it from the recent list?",
                parent=self.root,
            ):
                self.prefs.forget_file(path)
                self._rebuild_recent_menu()
            return
        if self.dirty:
            self.save()
        self._load_into(Path(path))

    def open_file_dialog(self) -> None:
        start = self.store.path.parent if self.store else Path.home()
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Open session directory",
            initialdir=str(start),
            filetypes=[("Session directory", "*.json"), ("All files", "*.*")],
        )
        if chosen:
            self.open_path(Path(chosen))

    def new_file(self) -> None:
        """Create an empty directory file and switch to it."""
        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title="New session directory",
            defaultextension=".json",
            initialfile="sessions.json",
            filetypes=[("Session directory", "*.json"), ("All files", "*.*")],
        )
        if not chosen:
            return
        if self.dirty:
            self.save()
        try:
            Store(Path(chosen)).create_empty()
        except StoreError as error:
            messagebox.showerror("New directory", str(error), parent=self.root)
            return
        self._load_into(Path(chosen))

    def save_as(self) -> None:
        """Write the open directory to a new file and continue editing there."""
        if not self.has_file():
            return
        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save session directory as",
            defaultextension=".json",
            initialfile=self.store.path.name,
            initialdir=str(self.store.path.parent),
            filetypes=[("Session directory", "*.json"), ("All files", "*.*")],
        )
        if not chosen:
            return
        store = Store(Path(chosen))
        try:
            store.save(self.directory)
        except StoreError as error:
            messagebox.showerror("Save as", str(error), parent=self.root)
            return
        self.store = store
        self.dirty = False
        self.prefs.remember_file(store.path)
        self._sync_file_state()
        self.refresh()
        self.status.set(f"Saved as {store.path.name}")

    def close_file(self) -> None:
        if not self.has_file():
            return
        if self.dirty:
            self.save()
        self.store = None
        self.directory = Directory()
        self.dirty = False
        self.filter_var.set("")
        self._sync_file_state()
        self.refresh(keep_selection=False)

    def save_now(self) -> None:
        """The Save menu item. Changes are written as you make them, so this
        only matters for the expand/collapse state and as reassurance."""
        if not self.has_file():
            return
        self.save()
        self.status.set(f"Saved {self.store.path.name}")

    def _require_file(self) -> bool:
        if self.has_file():
            return True
        messagebox.showinfo(
            "No directory open",
            "Open a session directory first, or create one with "
            "File > New directory.",
            parent=self.root,
        )
        return False

    def edit_settings(self) -> None:
        detected = launcher.find_putty(self.prefs.get("putty_path", ""))
        updated = SettingsDialog(self.root, dict(self.prefs.data), detected).show()
        if updated is None:
            return
        self.prefs.data = updated
        self.prefs.save()
        self.refresh()

    def open_config_folder(self) -> None:
        if not self.has_file():
            return
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
        if self.store is None:
            return  # Nothing open; edits are blocked anyway.
        try:
            self.store.save(self.directory)
            self.dirty = False
        except StoreError as error:
            messagebox.showerror("Save failed", str(error), parent=self.root)

    # -- ui scale ----------------------------------------------------------

    def set_scale(self, factor: float) -> None:
        """Scale the whole window to ``factor`` and remember it."""
        previous = self.scale.factor
        applied = self.scale.apply(factor)
        self.scale_var.set(applied)
        if applied == previous:
            return
        self._rescale_widgets()
        self._pending_scale = applied
        self._save_scale_soon()

    def nudge_scale(self, delta: float) -> None:
        self.set_scale(self.scale.factor + delta)

    def _apply_tree_metrics(self) -> None:
        """The tree's pixel measurements, which no font change touches."""
        self.style.configure("Treeview", rowheight=self.scale.row_height())
        self.style.configure("Treeview.Item",
                             indicatorsize=self.scale.px(self._base_indicator))
        for column, width in BASE_COLUMNS.items():
            self.tree.column(column, width=self.scale.px(width))

    def _rescale_widgets(self) -> None:
        """Resize what the font change does not cover: pixel measurements."""
        self._apply_tree_metrics()
        self.root.minsize(*self.scale.dims(*BASE_MINSIZE))

        # Resize the window, or the larger text just gets less room. This is
        # the default size at the new scale - the same rule __init__ uses -
        # rather than the current size times a ratio: the window manager clamps
        # what it cannot fit, and scaling off a clamped size compounds that
        # error until Reset no longer returns to where it started.
        if not self.root.winfo_viewable():
            return
        width, height = self.scale.dims(*BASE_WINDOW)
        self.root.geometry("{}x{}".format(
            min(width, self.root.winfo_screenwidth()),
            min(height, self.root.winfo_screenheight()),
        ))

    def _save_scale_soon(self) -> None:
        """Debounce the write: Ctrl+/- autorepeats, and each step would save."""
        if getattr(self, "_scale_save_job", None) is not None:
            self.root.after_cancel(self._scale_save_job)
        self._scale_save_job = self.root.after(400, self._save_scale_now)

    def _save_scale_now(self) -> None:
        self._scale_save_job = None
        self.prefs.set("ui_scale", self._pending_scale)

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
        if not self.prefs.get("tray_hint_shown"):
            self.prefs.set("tray_hint_shown", True)
            self.tray.notify(
                "Still running here. Right-click for your sessions."
            )

    def add_session_from_tray(self) -> None:
        """Tray shortcut: show the window, then open a blank session dialog."""
        self.show_window()
        self.add_session()

    def on_close(self) -> None:
        """The window's X button: hide to the tray if there is one."""
        if self.tray is not None and self.prefs.get("close_to_tray", True):
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
    root = _new_root()
    if root is None:
        return 1

    prefs = Preferences.load()
    store = Store(config_path)
    try:
        directory = store.load()
    except StoreError as error:
        # A broken or unreadable file should not stop the app from starting -
        # open with nothing loaded so File > Open still works.
        root.withdraw()
        messagebox.showerror("PuTTY Directory", f"{error}\n\nStarting with no directory open.")
        root.deiconify()
        prefs.forget_file(config_path)
        store, directory = None, Directory()

    if store is not None:
        prefs.remember_file(store.path)

    try:
        ttk.Style().theme_use("vista" if root.tk.call("tk", "windowingsystem") == "win32" else "clam")
    except tk.TclError:
        pass

    app = App(root, store, directory, start_hidden=start_hidden, prefs=prefs)
    if start_hidden and app.tray is None:
        messagebox.showwarning(
            "Tray unavailable",
            "Could not start a tray icon, so the window is shown instead.\n\n"
            + tray_module.unavailable_reason(),
            parent=root,
        )
    root.mainloop()
    return 0
