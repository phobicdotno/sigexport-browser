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


def _sig(p: Path):
    """(size, mtime_ns, WAL salts) of a file; None if absent."""
    try:
        st = p.stat()
    except FileNotFoundError:
        return None
    head = b""
    if p.name.endswith("-wal"):
        with open(p, "rb") as f:
            head = f.read(32)[16:24]  # salt-1/salt-2 change on every WAL reset (checkpoint restart)
    return st.st_size, st.st_mtime_ns, head


def copy_sql_live(src: Path, dst: Path, attempts: int = 6, pause: float = 2.0) -> int:
    """Consistent copy of sql/ while Signal may be running, without writing to Signal's folder.

    SQLite in WAL mode only changes db.sqlite during a checkpoint; between checkpoints it only
    appends frames to db.sqlite-wal. So: stat db + wal, copy db then wal (never -shm; SQLite
    rebuilds the wal-index from the WAL), stat again. If db.sqlite and the WAL salts are
    unchanged, the db copy is the pre-checkpoint image and the WAL copy is a prefix of the
    log; SQLite's WAL recovery ignores a torn trailing transaction (frame checksums), so the
    copy opens at the last fully committed transaction. Otherwise retry. Returns attempts used."""
    names = [n.name for n in src.iterdir() if n.is_file() and not n.name.endswith("-shm")]
    for i in range(1, attempts + 1):
        db_before, wal_before = _sig(src / "db.sqlite"), _sig(src / "db.sqlite-wal")
        tmp = dst.with_name(dst.name + ".new")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for n in sorted(names, key=lambda x: x.endswith("-wal")):  # db first, WAL last
            if (src / n).exists():
                shutil.copy2(src / n, tmp / n)
        db_after, wal_after = _sig(src / "db.sqlite"), _sig(src / "db.sqlite-wal")
        wal_ok = (wal_before is None and wal_after is None) or (
            wal_before is not None and wal_after is not None and wal_before[2] == wal_after[2]
            and wal_after[0] >= wal_before[0])
        if db_before == db_after and wal_ok:
            shutil.rmtree(dst, ignore_errors=True)
            os.replace(tmp, dst)
            return i
        shutil.rmtree(tmp, ignore_errors=True)
        time.sleep(pause)
    raise SystemExit("Signal kept checkpointing during the copy; retry later or use --quit-signal")


def prepare(signal_dir: Path, work_dir: Path, quit_signal: bool = True) -> None:
    """quit_signal=False: never quit Signal, use the live WAL-aware copy (copy_sql_live)."""
    if not (signal_dir / "config.json").is_file() or not (signal_dir / "sql").is_dir():
        raise SystemExit(f"Not a Signal Desktop folder: {signal_dir}")
    if quit_signal and signal_running():
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
    for f in COPY_FILES:
        (work_dir / f).unlink(missing_ok=True)
        if (signal_dir / f).is_file():
            shutil.copy2(signal_dir / f, work_dir / f)
    if quit_signal and not signal_running():
        shutil.rmtree(work_dir / "sql", ignore_errors=True)
        shutil.copytree(signal_dir / "sql", work_dir / "sql")
    else:
        n = copy_sql_live(signal_dir / "sql", work_dir / "sql")
        print(f"sql: live copy (attempts={n})")
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
