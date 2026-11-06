#!/usr/bin/env python3
"""Prepare a working copy of the Signal Desktop folder for sigexport (macOS/Linux/Windows).

Copies config.json, "Local State" (Windows: holds the DPAPI-protected key sigexport needs)
and sql/ (small) into WORK_DIR, and links the big attachments.noindex / stickers.noindex
folders: symlink, else a Windows directory junction (no admin / developer mode needed).
Nothing in the Signal folder is modified.

Is this step needed? Not strictly: sigexport 3.9.3 accepts `--source <Signal dir>` directly
(main.py reads <source>/config.json, data.py opens <source>/sql/db.sqlite, files.py reads
<source>/attachments.noindex). But it opens the live DB read-write with sqlcipher3 while
Signal may be running, so a copy is the safer default. If linking fails, export_all.py can
be pointed straight at the Signal folder with Signal closed.

Usage: prepare_source.py [SIGNAL_DIR] [WORK_DIR]
  SIGNAL_DIR default: auto-detected per OS (see platform_util.py)
  WORK_DIR   default: $OUT_DIR/_work/src (OUT_DIR default ~/SignalExport; never /tmp)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from platform_util import find_signal_dir  # noqa: E402

COPY_FILES = ("config.json", "Local State")
LINK_DIRS = ("attachments.noindex", "stickers.noindex")


def signal_running() -> bool:
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Signal.exe", "/NH"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
            return "Signal.exe" in out
        if shutil.which("pgrep"):
            flag = ["-x", "Signal"] if sys.platform == "darwin" else ["-x", "signal-desktop"]
            return subprocess.run(["pgrep", *flag], capture_output=True).returncode == 0
    except OSError:
        pass
    return False


def link_dir(target: Path, link: Path) -> str:
    """Create link -> target. Returns 'symlink', 'junction' or raises OSError."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except OSError:
        if sys.platform != "win32":
            raise
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if r.returncode != 0:
        raise OSError("mklink /J failed")
    return "junction"


def is_junction(p: Path) -> bool:
    return bool(getattr(os.path, "isjunction", lambda _p: False)(p))


def remove_link(p: Path) -> None:
    if p.is_symlink():
        p.unlink()
    elif is_junction(p):
        os.rmdir(p)  # removes the junction itself, never the target's contents
    elif p.exists():
        raise SystemExit(f"Refusing to replace a real directory: {p}")


def prepare(signal_dir: Path, work_dir: Path) -> None:
    if not (signal_dir / "config.json").is_file() or not (signal_dir / "sql").is_dir():
        raise SystemExit(f"Not a Signal Desktop folder: {signal_dir}")
    if signal_running():
        if sys.platform == "darwin":
            print("Signal Desktop is running. Quitting it so the database copy is consistent...")
            subprocess.run(["osascript", "-e", 'quit app "Signal"'], capture_output=True)
            for _ in range(30):
                if not signal_running():
                    break
                time.sleep(1)
        if signal_running():
            print("WARNING: Signal is still running; quit it, or the copied database may be inconsistent.",
                  file=sys.stderr)
    work_dir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(work_dir / "sql", ignore_errors=True)
    for f in COPY_FILES:
        (work_dir / f).unlink(missing_ok=True)
        if (signal_dir / f).is_file():
            shutil.copy2(signal_dir / f, work_dir / f)
    shutil.copytree(signal_dir / "sql", work_dir / "sql")
    for d in LINK_DIRS:
        link = work_dir / d
        remove_link(link)
        if (signal_dir / d).is_dir():
            try:
                kind = link_dir(signal_dir / d, link)
            except OSError:
                raise SystemExit(f"Could not link {d}. Quit Signal and run export_all.py with "
                                 f"--src pointing at the Signal folder itself instead.")
            print(f"{d}: {kind}")
    print(f"Prepared: {work_dir}")
    print(f'Next: python3 {Path(__file__).with_name("export_all.py")} --src "{work_dir}"')


def main(argv: list[str]) -> int:
    sig = Path(argv[0]).expanduser() if argv else (
        Path(os.environ["SIGNAL_DIR"]).expanduser() if os.environ.get("SIGNAL_DIR") else find_signal_dir())
    if sig is None:
        raise SystemExit("Signal Desktop folder not found; pass it as the first argument")
    work = Path(argv[1]).expanduser() if len(argv) > 1 else Path(
        os.environ.get("WORK_DIR")
        or Path(os.environ.get("OUT_DIR") or Path.home() / "SignalExport").expanduser() / "_work" / "src").expanduser()
    prepare(sig, work)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
