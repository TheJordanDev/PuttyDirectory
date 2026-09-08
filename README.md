# PuTTY Directory

A standalone session manager for PuTTY: organise your connections into nested
folders and launch them from a tray icon, the way Pageant does. It keeps its own
JSON file and never touches PuTTY's own saved sessions, so you can keep a work
set completely apart from a personal one.

## Running

```sh
python main.py                            # window + tray icon
python main.py --tray                     # start minimised to the tray
python main.py --config work.json         # a separate, self-contained set
python main.py --list                     # print the tree, no GUI
python main.py --connect "Work/Alpha/web1"  # launch a session directly
```

The tray icon needs `pystray` and `Pillow` (`uv sync`, or `pip install pystray
pillow`). Without them everything else still works — you just get a plain window
and *Hide to tray* is greyed out.

## The tray icon

This is the quick-launch path, and it mirrors Pageant's behaviour:

- **Right-click** the icon for your whole session tree as nested submenus.
  Clicking a session launches it. Folders become submenus, at any depth.
- **Left-click** (or double-click) opens the manager window.
- A **Recent** submenu sits at the top with the last five sessions you connected
  to, each labelled with the folder path it came from.
- Closing the window **hides to the tray** rather than quitting, so the icon
  stays put. *Exit* on the tray menu is what actually quits. Turn this off in
  *File > Settings* if you would rather the X button quit outright.

On Windows a new tray icon usually starts in the hidden-icons overflow (the `^`
chevron); drag it onto the taskbar to keep it visible. To have it there at login,
point a shortcut at `pythonw main.py --tray` and drop it in `shell:startup`.

### Starting in the tray on Linux

```sh
./install-linux.sh --autostart      # start in the tray at every login
~/.local/bin/puttydirectory --tray  # or start it now, by hand
```

`--tray` is the same flag as on Windows. The catch is that a tray icon needs a
**graphical session**: run it from inside your desktop, not over SSH and not from
a text console. Check with `echo $DISPLAY` - if that prints nothing you will get:

```text
Xlib.error.DisplayNameError: Bad display name ""
```

Autostarting through `~/.config/autostart` avoids this entirely, because the
desktop session sets `DISPLAY` before launching anything in it. That is what
`install-linux.sh --autostart` sets up.

On Linux, pystray picks a backend at import: AppIndicator if `gi` and
`AyatanaAppIndicator3` are present, otherwise GTK or plain XOrg. GNOME shows no
tray at all without the AppIndicator extension, so on stock GNOME install
`gir1.2-ayatanaappindicator3-0.1` and the extension, or force a backend with
`PYSTRAY_BACKEND=xorg`. If none of that works the app falls back to window-only
rather than failing. For autostart, a `.desktop` file in `~/.config/autostart`
running `python main.py --tray` is the equivalent of the Windows shortcut.

## Working with several directories

Sessions live in ordinary JSON files, and the File menu treats them as
documents:

| Action | Shortcut |
| --- | --- |
| New directory... | `Ctrl+Shift+O` |
| Open... | `Ctrl+O` |
| Open recent | - |
| Close | `Ctrl+W` |
| Save | `Ctrl+S` |
| Save as... | `Ctrl+Shift+S` |

So a work set and a personal set are just two files you switch between, rather
than two shortcuts with different `--config` flags. **Open recent** remembers the
last ten, showing each file's folder alongside its name because they are all
likely to be called something like `sessions.json`. A file that has since been
deleted offers to drop itself from the list.

The window title and the tray tooltip both name the open file, so two running
copies are tellable apart. With nothing open the tree is empty and the editing
buttons grey out; **Save as** copies the open directory to a new file and keeps
editing there.

Edits are still written to disk as you make them - **Save** is only there for
reassurance and to flush the expand/collapse state.

## Where the sessions are stored

The first of these that applies wins:

1. `--config PATH`
2. the `PUTTYDIR_CONFIG` environment variable
3. `config.json` in the current directory, if one exists
4. the file you had open last
5. the per-user default:
   - Windows: `%APPDATA%\PuttyDirectory\directory.json`
   - Linux: `~/.config/PuttyDirectory/directory.json`
   - macOS: `~/Library/Application Support/PuttyDirectory/directory.json`

The file is plain JSON and diffs cleanly, so it is safe to keep a work directory
in a private git repo.

### App settings vs directory contents

App-level settings - the PuTTY path, the confirm-on-delete and close-to-tray
toggles, and the recent-files list - live in `preferences.json` next to the
default directory file, **not** inside whichever directory is open. They used to
live in the directory file, which meant opening a second one silently changed
your PuTTY path. Old files are migrated the first time they are opened, so
nothing is lost.

What stays in the directory file is what belongs to it: the tree, and which of
its own sessions you connected to most recently.

An older flat `{"entries": [...]}` file is migrated to the tree format the first
time it is loaded.

## Folders and inheritance

Folders nest arbitrarily deep, which is the point: `Client / Project / Staging /
web1`. A folder carries the same connection fields as a session, and any field a
session leaves empty is inherited from the nearest ancestor that sets it.

So a `Project Alpha` folder can define `user`, `port` and the private key once,
and every session inside it just needs a host. Inherited values are shown greyed
out in the list, and the dialog shows the value that would be inherited next to
each empty field.

## Organising

| Action | How |
| --- | --- |
| Move anywhere | Drag and drop. Dropping on a folder moves *into* it |
| Reorder | `Ctrl+Up` / `Ctrl+Down` |
| Move into the folder above | `Ctrl+Right` |
| Move out of the current folder | `Ctrl+Left` |
| New session / folder | `Ctrl+N` / `Ctrl+Shift+N` |
| Edit / duplicate / delete | `F2` / `Ctrl+D` / `Del` |
| Connect | `Enter`, or double-click |
| Filter | `Ctrl+F` |
| Hide to tray | the window's X button |

New items are created inside the selected folder, or next to the selected
session. Drag and drop is disabled while a filter is active, since the visible
tree is then not the real one.

Every change is written to disk immediately.

## Connecting

*Connect* builds a PuTTY command line from the resolved session and launches it
detached, so PuTTY keeps running if you close this app. *Session > Show command
line* prints exactly what would run (with the password masked) and copies it to
the clipboard — useful when a connection does not behave as expected.

The command looks like:

```text
putty -ssh -P 2222 -l jordan -i C:\keys\alpha.ppk 10.0.0.5
```

PuTTY itself is located from, in order: the path set in *File > Settings*, then
`$PATH`, then the usual install locations. The status bar bottom-right says
whether it was found.

`Load PuTTY session` maps to `-load`, applied before the other flags, so you can
build on an existing PuTTY session and override parts of it here. `Extra
arguments` is appended verbatim (split shell-style) for anything not covered,
e.g. `-X` or `-L 8080:localhost:80`.

## A note on passwords

Passwords are stored **in plain text** in the JSON file, and passed to PuTTY on
the command line via `-pw`, where they are briefly visible in the process list.
This is a limitation of how PuTTY accepts passwords, not something this app can
work around.

Prefer a private key (`.ppk`) per session or per folder. If you do store
passwords, treat the directory file as a secret: keep it out of any shared
repository, and note that `.gitignore` here excludes `config.json` and
`directory.json` for that reason.
