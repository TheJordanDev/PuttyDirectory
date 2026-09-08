# Building a standalone executable

```sh
python build.py             # dist/PuttyDirectory.exe          9.0 MB
python build.py --onedir    # dist/PuttyDirectory/            19.9 MB, starts faster
python build.py --clean     # throw away cached analysis first
```

`build.py` renders `assets/icon.ico` from the same code that draws the tray
icon, writes the Windows version resource, then runs PyInstaller against
[PuttyDirectory.spec](PuttyDirectory.spec). Both generated files are gitignored;
the icon has one definition, in `puttydirectory/tray.py`.

## What the tuning achieved

A stock `pyinstaller --windowed --onefile main.py` produces **18.9 MB**. The spec
brings that to **9.0 MB** — 52% smaller — without changing a line of app code.

| Component | Stock | Tuned | Note |
| --- | ---: | ---: | --- |
| PIL | 13.87 MB | 3.58 MB | unused image codecs |
| libcrypto + libssl | 6.55 MB | 0 | OpenSSL, for an app that opens no sockets |
| Tcl data | 3.21 MB | 1.73 MB | 609 timezone files |
| everything else | 20.8 MB | 20.1 MB | Python, Tk, encodings |
| **uncompressed** | **44.5 MB** | **25.4 MB** | |
| **onefile .exe** | **18.9 MB** | **9.0 MB** | after PyInstaller's compression |

### Where the savings come from

**Pillow codecs — 10.3 MB.** `PIL/_avif.pyd` alone is 7.5 MB, plus FreeType
(2.1 MB), WebP and LittleCMS. We only draw a small image and save PNG/ICO, so
none are reachable. Pillow imports every one of them behind `try/except
ImportError`, so excluding them degrades gracefully rather than crashing — that
was checked against Pillow's source before excluding, not assumed.

**OpenSSL — 6.5 MB.** `libcrypto-3-x64.dll` is pulled in by `_hashlib`, which
`hashlib` imports. The app never opens a socket. `hashlib` falls back to CPython's
built-in `_sha*` modules when `_hashlib` is missing, and `random` prefers those
anyway (`from hashlib import sha512` there is a fallback inside a `try`). Verified
by blocking the imports and confirming `hashlib`, `random` and `uuid4` still work.

**Tcl timezone tables — 1.5 MB, 740 files.** `_tcl_data/tzdata` is 609 files of
zoneinfo for Tcl's `clock` command, which no Tkinter widget reaches.

**`optimize=2`** strips docstrings and asserts from the bundled bytecode.

### One exclude that does not work

`urllib` looks unused, but `pathlib` imports `urllib.parse` for `Path.as_uri()`.
Excluding it fails at startup with `ModuleNotFoundError: No module named
'urllib'` before any window appears. There is a comment in the spec so nobody
re-adds it. If you add excludes, always run the result — PyInstaller reports a
successful build for a bundle that cannot start.

## onefile or onedir

Measured over 4 runs each, launch until the window is on screen:

| | Size | Startup | Files |
| --- | ---: | ---: | ---: |
| onefile | 9.0 MB | **445 ms** | 1 |
| onedir | 19.9 MB | **236 ms** | 216 |

onefile unpacks itself into `%TEMP%` on every launch, which is the extra ~210 ms.
It also runs as two processes: a bootloader parent plus the real app.

**onefile is the default and the right pick here.** A tray app is started once
and left alone, so 445 ms is paid once per login, and a single file is far easier
to drop into `shell:startup` or copy to another machine. Choose `--onedir` only
if you are launching it repeatedly, or if antivirus scanning of the temp
extraction becomes annoying.

## Deliberately not enabled

**UPX.** It would shave a few more MB, but UPX-packed PyInstaller binaries are one
of the most reliable ways to get flagged by antivirus heuristics. For something
that autostarts and sits in the tray, a false positive costs more than 2 MB
saves. `upx=False` is set explicitly in the spec.

**`strip=True`.** No effect on Windows binaries; it only costs readable
tracebacks.

## Verifying a build

The GUI is windowed, so a broken bundle fails silently. These check it for real:

```sh
dist/PuttyDirectory.exe --list       # exercises imports, model, config loading
dist/PuttyDirectory.exe --version
```

Those work because the app calls `AttachConsole(ATTACH_PARENT_PROCESS)` when it
is started with arguments — a windowed build has no console of its own, so
without that `--list` would print into nothing. Double-clicking still opens
silently with no console flash.

To confirm the GUI and tray really came up, check the window classes of the
running process: a working build has a visible `TkTopLevel` and a hidden
`puttydirectory<n>SystemTrayIcon` (registered by pystray). Both were confirmed
present in the frozen build, along with `pystray._win32` being found — it is
listed in `hiddenimports` because pystray picks its backend dynamically and
static analysis cannot see it.

## Linux

```sh
./build-linux.sh              # dist/PuttyDirectory  (single file)
./build-linux.sh --onedir     # dist/PuttyDirectory/ (folder, starts faster)
./install-linux.sh --autostart   # into ~/.local, starting in the tray at login
./install-linux.sh --uninstall   # remove it again
```

`build-linux.sh` checks the prerequisites before building — PyInstaller will
happily produce a bundle that only fails when you run it — then creates a venv,
installs the dependencies and calls the same `build.py`.

### Build on the machine you will run it on

**This is the part that catches people out.** PyInstaller does not cross-compile
and it links against the build host's glibc, so a binary is only portable
*forwards*:

- built on glibc 2.41 (Debian 13) → will not start on Ubuntu 22.04 (2.35)
- built on glibc 2.31 (Ubuntu 20.04) → runs on everything newer

You get `/lib/x86_64-linux-gnu/libc.so.6: version 'GLIBC_2.38' not found` at
launch, with no other clue. So: **build on the target machine**, or on the oldest
distro you need to support. `build.py` prints the glibc it built against.

There is nothing to compile here — it is pure Python — so building on the target
is genuinely the easy path: copy the source over, run `./build-linux.sh`. If you
need one binary for several machines, build it in a container of the oldest one
(`docker run --rm -v "$PWD:/src" -w /src ubuntu:22.04 ...`).

You do not have to freeze it at all, incidentally. `python main.py --tray` from a
venv works fine and skips every one of these concerns; the executable is a
convenience for machines where you would rather not manage a Python environment.

### Prerequisites

`tkinter` is packaged separately from Python on most distros, which is the other
common surprise:

| Distro | Packages |
| --- | --- |
| Debian / Ubuntu | `sudo apt install python3 python3-venv python3-pip python3-tk` |
| Fedora | `sudo dnf install python3 python3-tkinter` |
| Arch | `sudo pacman -S python tk` |

Plus PuTTY itself at runtime: `apt install putty` (this app launches it, it does
not reimplement it).

**`python3-venv` matters more than it looks.** Debian and Ubuntu strip
`ensurepip` out of the standard library and ship it in that package. Without it,
`python3 -m venv` creates `bin/` and *then* fails, leaving an environment with no
pip in it — which surfaces later as:

```text
.venv/bin/python: No module named pip
```

`build-linux.sh` checks for `ensurepip` before it does anything, and validates
the venv by actually running `pip --version` in it rather than trusting that the
directory exists. A venv that fails that check is deleted and rebuilt, so a
half-created one from an earlier failed run cannot poison later builds.

The Linux venv is `.venv-linux`, deliberately not `.venv`: the project folder is
likely to be shared with a Windows checkout, and the two layouts (`bin/` vs
`Scripts/`) are not interchangeable. A Windows `.venv` copied across is detected
and replaced rather than half-used.

### The tray backend

pystray needs one of three backends, and the choice matters when freezing:

- **`_xorg`** — needs `python-xlib`, a pip package. Bundles cleanly, so this is
  what the spec picks and what `pyproject.toml` installs on Linux. Talks X11,
  which works on X sessions and through XWayland on Wayland ones.
- **`_appindicator` / `_gtk`** — need PyGObject and system GTK libraries.
  Painful to freeze and much larger. The spec includes them only if `gi` is
  already importable.

The spec picks backends by probing for `Xlib` and `gi` rather than importing
pystray, because importing pystray runs its own backend selection and raises
when none is usable. Force one with `PYSTRAY_BACKEND=xorg` at build time.

**GNOME shows no tray icons at all** without the AppIndicator extension, whatever
backend you use — that is a GNOME policy, not a bug here. On GNOME install the
extension plus `gir1.2-ayatanaappindicator3-0.1`; KDE, XFCE, Cinnamon and MATE
all work out of the box. If no backend starts, the app falls back to a plain
window rather than failing, and *Hide to tray* is greyed out.

### Icons and autostart

An ELF has no embedded icon, so the `icon=` in the spec is skipped on Linux and
`build.py` emits `dist/puttydirectory.desktop` and `dist/puttydirectory.png`
instead. The desktop entry uses `Icon=puttydirectory` — a theme name, not a path
— so the icon survives the binary being moved. `install-linux.sh` puts the PNG in
`~/.local/share/icons/hicolor/256x256/apps/` where that lookup finds it, rewrites
`Exec=` to the installed path, and refreshes the desktop and icon caches.

## Other packagers

**Nuitka** is the one genuinely worth trying if you want to go further. It
compiles to C rather than bundling an interpreter, and typically gives faster
startup and a somewhat smaller binary:

```sh
python -m nuitka --standalone --onefile --enable-plugin=tk-inter \
    --windows-console-mode=disable --windows-icon-from-ico=assets/icon.ico main.py
```

It needs a C compiler (MSVC, or it offers to fetch MinGW) and compiles for
several minutes rather than seconds. I have not measured it on this project, so
treat the improvement as plausible rather than established — the excludes above
are where the large, certain wins already are, and Nuitka is a slower
edit-build-test loop for what is likely a smaller further gain.

**cx_Freeze** is a reasonable onedir alternative but has no real advantage over
PyInstaller here. **py2exe** is Windows-only, which conflicts with the Linux
goal. **Briefcase** targets installers and app-store packaging — more machinery
than a tray utility needs.
