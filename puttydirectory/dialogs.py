"""Modal dialogs for editing nodes and application settings."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from .model import PROTOCOLS, Node


class ModalDialog(tk.Toplevel):
    """A Toplevel that grabs focus and blocks until closed."""

    def __init__(self, parent: tk.Misc, title: str):
        super().__init__(parent)
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.result = None
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.bind("<Escape>", lambda _event: self.on_cancel())

    def show(self):
        self.update_idletasks()
        self._centre()
        self.grab_set()
        self.wait_window(self)
        return self.result

    def _centre(self) -> None:
        parent = self.master.winfo_toplevel()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def on_cancel(self) -> None:
        self.result = None
        self.destroy()


class NodeDialog(ModalDialog):
    """Create or edit a folder or a session.

    Folders show the same connection fields as sessions (minus the host); values
    set there are inherited by everything inside the folder.
    """

    def __init__(self, parent: tk.Misc, node: Node, inherited: dict | None = None):
        kind = "Folder" if node.is_folder else "Session"
        super().__init__(parent, f"{kind}: {node.name or 'New'}")
        self.node = node
        self.inherited = inherited or {}
        self.vars: dict[str, tk.StringVar] = {}

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        row = 0
        row = self._add_field(body, row, "name", "Name")
        if not node.is_folder:
            row = self._add_field(body, row, "host", "Host")

        row = self._add_separator(body, row, "Connection")
        if node.is_folder:
            hint = ttk.Label(
                body,
                text="Leave a field empty to inherit it. Values set here apply\n"
                     "to every session inside this folder unless overridden.",
                foreground="#555555",
                justify="left",
            )
            hint.grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 8))
            row += 1

        row = self._add_field(body, row, "user", "User")
        row = self._add_field(body, row, "port", "Port")
        row = self._add_field(body, row, "password", "Password", secret=True)
        row = self._add_field(body, row, "key_file", "Private key", browse="key")
        row = self._add_protocol(body, row)

        row = self._add_separator(body, row, "Advanced")
        row = self._add_field(body, row, "putty_session", "Load PuTTY session")
        row = self._add_field(body, row, "extra_args", "Extra arguments")
        row = self._add_notes(body, row)

        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.on_cancel).pack(side="right")
        ttk.Button(buttons, text="Save", command=self.on_save).pack(side="right", padx=(0, 6))

        self.bind("<Return>", lambda _event: self.on_save())
        self.after(10, lambda: self.name_entry.focus_set())

    # -- field builders ---------------------------------------------------

    def _add_separator(self, body: ttk.Frame, row: int, text: str) -> int:
        ttk.Separator(body, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=(10, 4)
        )
        ttk.Label(body, text=text, foreground="#666666").grid(
            row=row + 1, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        return row + 2

    def _add_field(self, body: ttk.Frame, row: int, key: str, label: str,
                   secret: bool = False, browse: str | None = None) -> int:
        variable = tk.StringVar(value=getattr(self.node, key, ""))
        self.vars[key] = variable

        ttk.Label(body, text=f"{label}:").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
        entry = ttk.Entry(body, textvariable=variable, width=34, show="*" if secret else "")
        entry.grid(row=row, column=1, sticky="ew", pady=2)
        if key == "name":
            self.name_entry = entry

        if browse == "key":
            ttk.Button(body, text="...", width=3, command=self._browse_key).grid(
                row=row, column=2, sticky="w", padx=(4, 0)
            )
        elif self.inherited.get(key) and not getattr(self.node, key, ""):
            ttk.Label(body, text=f"({self.inherited[key]})", foreground="#888888").grid(
                row=row, column=2, sticky="w", padx=(4, 0)
            )
        return row + 1

    def _add_protocol(self, body: ttk.Frame, row: int) -> int:
        variable = tk.StringVar(value=self.node.protocol)
        self.vars["protocol"] = variable
        ttk.Label(body, text="Protocol:").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
        combo = ttk.Combobox(body, textvariable=variable, values=("",) + PROTOCOLS,
                             state="readonly", width=32)
        combo.grid(row=row, column=1, sticky="ew", pady=2)
        default = self.inherited.get("protocol") or "ssh"
        ttk.Label(body, text=f"({default})", foreground="#888888").grid(
            row=row, column=2, sticky="w", padx=(4, 0)
        )
        return row + 1

    def _add_notes(self, body: ttk.Frame, row: int) -> int:
        ttk.Label(body, text="Notes:").grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=2)
        self.notes = tk.Text(body, width=34, height=3, wrap="word")
        self.notes.insert("1.0", self.node.notes)
        self.notes.grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)
        return row + 1

    def _browse_key(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Select private key",
            filetypes=[("PuTTY private key", "*.ppk"), ("All files", "*.*")],
        )
        if path:
            self.vars["key_file"].set(path)

    # -- actions ----------------------------------------------------------

    def on_save(self) -> None:
        values = {key: variable.get().strip() for key, variable in self.vars.items()}
        values["notes"] = self.notes.get("1.0", "end").strip()
        if not values["name"]:
            values["name"] = values.get("host") or ("New folder" if self.node.is_folder else "New session")
        for key, value in values.items():
            setattr(self.node, key, value)
        self.result = self.node
        self.destroy()


class SettingsDialog(ModalDialog):
    def __init__(self, parent: tk.Misc, settings: dict, detected: str | None):
        super().__init__(parent, "Settings")
        self.settings = settings

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="PuTTY executable:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.putty_var = tk.StringVar(value=settings.get("putty_path", ""))
        ttk.Entry(body, textvariable=self.putty_var, width=44).grid(row=0, column=1, sticky="ew")
        ttk.Button(body, text="...", width=3, command=self._browse).grid(
            row=0, column=2, padx=(4, 0)
        )

        found = detected or "not found on this system"
        ttk.Label(body, text=f"Leave empty to auto-detect. Currently: {found}",
                  foreground="#666666").grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.confirm_var = tk.BooleanVar(value=settings.get("confirm_delete", True))
        ttk.Checkbutton(body, text="Ask before deleting", variable=self.confirm_var).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(10, 0)
        )

        self.tray_var = tk.BooleanVar(value=settings.get("close_to_tray", True))
        ttk.Checkbutton(
            body,
            text="Closing the window hides to the tray instead of quitting",
            variable=self.tray_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w")

        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.on_cancel).pack(side="right")
        ttk.Button(buttons, text="Save", command=self.on_save).pack(side="right", padx=(0, 6))

    def _browse(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Select putty executable")
        if path:
            self.putty_var.set(path)

    def on_save(self) -> None:
        self.settings["putty_path"] = self.putty_var.get().strip()
        self.settings["confirm_delete"] = bool(self.confirm_var.get())
        self.settings["close_to_tray"] = bool(self.tray_var.get())
        self.result = self.settings
        self.destroy()

