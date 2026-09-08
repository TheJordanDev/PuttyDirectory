"""Build a standalone PuTTY Directory executable.

    python build.py             # one file:   dist/PuttyDirectory.exe
    python build.py --onedir    # one folder: dist/PuttyDirectory/  (starts faster)
    python build.py --clean     # discard cached analysis first

Generates the icon and Windows version resource, then runs PyInstaller against
PuttyDirectory.spec and reports the result.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

sys.path.insert(0, str(ROOT))
from puttydirectory import __version__  # noqa: E402


def write_icon() -> Path:
    """Render the tray icon to .ico (for the exe) and .png (for Linux desktops)."""
    from puttydirectory.tray import make_image

    ASSETS.mkdir(exist_ok=True)
    image = make_image(256)
    path = ASSETS / "icon.ico"
    image.save(path, format="ICO", sizes=ICON_SIZES)
    image.save(ASSETS / "icon.png", format="PNG")
    return path


def write_desktop_entry(binary: Path) -> Path:
    """Write a .desktop launcher and the icon it names.

    An ELF carries no embedded icon, so on Linux this file is what gives the app
    its name and icon in the launcher, and what autostart reads.

    ``Icon`` is a bare name, not a path: the desktop resolves it through the
    icon theme, so the artwork keeps working when the binary moves. install-
    linux.sh puts the PNG where that lookup will find it.
    """
    import shutil

    dist = ROOT / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS / "icon.png", dist / "puttydirectory.png")

    path = dist / "puttydirectory.desktop"
    path.write_text(
        f"""[Desktop Entry]
Type=Application
Name=PuTTY Directory
Comment=Organise and launch PuTTY sessions from the tray
Exec={binary} --tray
Icon=puttydirectory
Terminal=false
Categories=Network;RemoteAccess;Utility;
StartupNotify=false
""",
        encoding="utf-8",
    )
    return path


def write_version_info() -> Path | None:
    """Windows file properties, so the exe is not an anonymous 'main.exe'."""
    if sys.platform != "win32":
        return None
    parts = (__version__.split(".") + ["0", "0", "0"])[:4]
    numbers = ", ".join(str(int(part)) for part in parts)
    path = ASSETS / "version_info.txt"
    path.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({numbers}), prodvers=({numbers}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
        StringStruct('FileDescription', 'PuTTY Directory'),
        StringStruct('FileVersion', '{__version__}'),
        StringStruct('InternalName', 'PuttyDirectory'),
        StringStruct('OriginalFilename', 'PuttyDirectory.exe'),
        StringStruct('ProductName', 'PuTTY Directory'),
        StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return path


def human(size: int) -> str:
    return f"{size / 1048576:.1f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--onedir", action="store_true",
                        help="build a folder instead of a single file (faster startup)")
    parser.add_argument("--clean", action="store_true",
                        help="remove build/ and dist/ before building")
    args = parser.parse_args()

    if args.clean:
        for folder in (ROOT / "build", ROOT / "dist"):
            shutil.rmtree(folder, ignore_errors=True)

    print(f"icon    -> {write_icon()}")
    version_file = write_version_info()
    if version_file:
        print(f"version -> {version_file}")

    environment = dict(os.environ)
    environment["PUTTYDIR_ONEDIR"] = "1" if args.onedir else "0"

    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(ROOT / "PuttyDirectory.spec")]
    if args.clean:
        command.append("--clean")

    started = time.time()
    result = subprocess.run(command, cwd=ROOT, env=environment)
    if result.returncode != 0:
        print("\nBuild failed.", file=sys.stderr)
        return result.returncode

    elapsed = time.time() - started
    target = ROOT / "dist" / ("PuttyDirectory" if args.onedir else
                              ("PuttyDirectory.exe" if sys.platform == "win32" else "PuttyDirectory"))

    if target.is_dir():
        total = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
        count = sum(1 for f in target.rglob("*") if f.is_file())
        print(f"\nBuilt {target} in {elapsed:.0f}s - {human(total)} across {count} files")
        binary = target / "PuttyDirectory"
    elif target.exists():
        print(f"\nBuilt {target} in {elapsed:.0f}s - {human(target.stat().st_size)}")
        binary = target
    else:
        print(f"\nBuild reported success but {target} is missing.", file=sys.stderr)
        return 1

    if sys.platform.startswith("linux"):
        print(f"desktop -> {write_desktop_entry(binary)}")
        report_glibc()
    return 0


def report_glibc() -> None:
    """Warn about the binary's glibc floor.

    PyInstaller links against the build host's glibc, so the binary runs only on
    systems with that version or newer. This is the single most common reason a
    Linux build fails on the machine it was copied to.
    """
    try:
        import subprocess as sp

        out = sp.run(["ldd", "--version"], capture_output=True, text=True).stdout
        version = out.splitlines()[0].split()[-1] if out else "unknown"
    except (OSError, IndexError):
        version = "unknown"
    print(
        f"\nBuilt against glibc {version}. The binary will NOT run on a machine\n"
        f"with an older glibc - build on the oldest distro you need to support."
    )


if __name__ == "__main__":
    raise SystemExit(main())
