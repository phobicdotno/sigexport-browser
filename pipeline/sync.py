#!/usr/bin/env python3
"""Merge-only re-sync of an existing archive (OUT) from Signal Desktop. Never loses data.

Usage: python3 sync.py --out ~/SignalExport [--upload] [--dry-run] [--quit-signal]
       python3 sync.py --setup-key            (one-time, interactive; see README "Sync")

Steps: lock -> disk guard -> DB snapshot (OUT/_work/src) -> full sigexport into
OUT/_work/sync_tmp -> per chat: match to NNN (conversation id via _state/conv_map.tsv,
else the real subfolder name inside NNN_*/; ambiguous -> skip; unknown cid -> new NNN) -> append messages that
are not in data.json yet -> hard-link backup, write, central media, link check (restore
on failure) -> rebuild viewer -> optional rclone copy + check --one-way.

Merge rules (same keys as the phone merge): a new message matches an existing one when
  date (ms) + sender are equal, or
  sender and body are equal and the dates are within 60 s, or
  both carry a server timestamp (server_ts / serverTimestamp / sent_at) and it is equal.
Existing lines of data.json / chat.md are never changed or removed (phone-backup lines
included); missing attachments only matter on new messages.

Contract (used by the Sync button / launchd job):
  lock   OUT/_state/sync.lock (PID); busy -> exit 75
  status OUT/_state/sync_status.json (atomic, after every step)
  log    OUT/_state/sync.log
Env as export_all.py (SIGEXPORT, SIGNAL_DIR, MIN_FREE_GIB, ABORT_FREE_GIB) plus REMOTE.
Logs counts and NNN only, never names or text.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import central_media as CM  # noqa: E402
import dedup as D  # noqa: E402
from platform_util import free_gib, safe_component  # noqa: E402

EX_BUSY = 75
WINDOW = 60.0
SERVER_KEYS = ("server_ts", "serverTimestamp", "sent_at")
MD_HEAD = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] ")
UPLOAD_EXCLUDES_FILE = HERE.parent / "tools" / "upload_excludes.txt"


def load_upload_excludes(path: Path = UPLOAD_EXCLUDES_FILE) -> list[str]:
    """rclone exclude patterns from tools/upload_excludes.txt (single source of truth, shared
    with upload.sh). Missing file or missing sync_config.js entry -> refuse (fail closed)."""
    pats = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    if "viewer/sync_config.js" not in pats:
        raise SystemExit(f"{path}: viewer/sync_config.js not excluded, refusing to upload")
    return pats


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- lock / status / log
class Busy(Exception):
    pass


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":  # os.kill(pid, 0) would terminate the process on Windows
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def acquire_lock(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except FileExistsError:
            try:
                pid = int(p.read_text().strip() or 0)
            except (OSError, ValueError):
                pid = 0
            if pid and pid != os.getpid() and pid_alive(pid):
                raise Busy(pid)
            p.unlink(missing_ok=True)  # stale lock
    raise Busy(0)


class Run:
    def __init__(self, out: Path, dry: bool):
        self.out, self.dry = out, dry
        self.state = out / "_state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.st = {"state": "running", "step": "start", "progress": 0.0, "started": now_iso(),
                   "finished": None, "added_messages": 0, "new_media": 0, "new_chats": 0,
                   "pruned_files": 0, "pruned_bytes": 0,
                   "message": "dry run" if dry else ""}
        self.upload_ok = False  # set by sync() only when rclone copy AND check --one-way passed
        self.write_status()

    def log(self, msg: str) -> None:
        line = f"{datetime.now():%F %T} {msg}"
        print(line, flush=True)
        with open(self.state / "sync.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def write_status(self) -> None:
        p = self.state / "sync_status.json"
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.st, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

    def step(self, step: str, progress: float, message: str | None = None) -> None:
        self.st["step"], self.st["progress"] = step, round(min(1.0, max(0.0, progress)), 3)
        if message is not None:
            self.st["message"] = message
        self.write_status()
        self.log(f"step={step} progress={self.st['progress']}")

    def end(self, ok: bool, message: str) -> None:
        self.st.update(state="done" if ok else "failed", finished=now_iso(), message=message)
        if ok:
            self.st["progress"] = 1.0
        self.write_status()
        self.log(f"end state={self.st['state']} added={self.st['added_messages']} "
                 f"media={self.st['new_media']} chats={self.st['new_chats']}")


# ---------------------------------------------------------------- data.json helpers
def read_jsonl(p: Path) -> list[tuple[str, dict]]:
    out = []
    if not p.is_file():
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            s = line.rstrip("\r\n")
            if s.strip():
                out.append((s, json.loads(s)))
    return out


def ts(m: dict) -> float:
    d = m.get("date") or ""
    try:
        return datetime.fromisoformat(d).timestamp()
    except ValueError:
        return 0.0


def date_key(m: dict) -> str:
    d = m.get("date") or ""
    try:
        return datetime.fromisoformat(d).isoformat(timespec="milliseconds")
    except ValueError:
        return d


def server_ts(m: dict):
    for k in SERVER_KEYS:
        if m.get(k) not in (None, ""):
            return m[k]
    return None


class Matcher:
    def __init__(self, old: list[dict]):
        self.keys = {(date_key(m), m.get("sender")) for m in old}
        self.srv = {server_ts(m) for m in old} - {None}
        self.by_sender: dict = {}
        for m in old:
            self.by_sender.setdefault(m.get("sender"), []).append((ts(m), m.get("body") or ""))
        for v in self.by_sender.values():
            v.sort()

    def known(self, m: dict) -> bool:
        if (date_key(m), m.get("sender")) in self.keys:
            return True
        s = server_ts(m)
        if s is not None and s in self.srv:
            return True
        lst = self.by_sender.get(m.get("sender"), [])
        t, body = ts(m), m.get("body") or ""
        i = bisect.bisect_left(lst, (t - WINDOW, ""))
        while i < len(lst) and lst[i][0] <= t + WINDOW:
            if lst[i][1] == body:
                return True
            i += 1
        return False


def new_messages(old: list[dict], new: list[dict]) -> list[dict]:
    mt = Matcher(old)
    out, seen = [], set()
    for m in new:
        k = (date_key(m), m.get("sender"), m.get("body"))
        if k in seen or mt.known(m):
            continue
        seen.add(k)
        out.append(m)
    out.sort(key=ts)
    return out


def merge_lines(old_lines: list[str], old_ts: list[float], add: list[tuple[float, str]]) -> list[str]:
    """Insert add (ts, line) after the last old line with ts <= t (old lines keep order and bytes).
    If old timestamps are not sorted, new lines are simply appended."""
    add = sorted(add, key=lambda x: x[0])
    if any(old_ts[i] > old_ts[i + 1] for i in range(len(old_ts) - 1)):
        return old_lines + [l for _t, l in add]
    slots: dict = {}
    for t, l in add:
        slots.setdefault(bisect.bisect_right(old_ts, t), []).append(l)
    res = []
    for i in range(len(old_lines) + 1):
        res.extend(slots.get(i, []))
        if i < len(old_lines):
            res.append(old_lines[i])
    return res


# ---------------------------------------------------------------- chat.md helpers
def md_blocks(text: str) -> list[tuple[float, str, str]]:
    """Split chat.md into (ts, header, block-text) per message; preamble has ts -inf."""
    blocks, cur, cur_ts, cur_head = [], [], float("-inf"), ""
    for line in text.splitlines(keepends=True):
        m = MD_HEAD.match(line)
        if m:
            if cur:
                blocks.append((cur_ts, cur_head, "".join(cur)))
            cur, cur_head = [line], line
            cur_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        else:
            cur.append(line)
    if cur:
        blocks.append((cur_ts, cur_head, "".join(cur)))
    return blocks


def md_insert(old_text: str, new_blocks: list[tuple[float, str]]) -> str:
    """Insert new blocks without touching existing bytes: each goes before the first old block
    with a later timestamp (else at the end)."""
    old = md_blocks(old_text)
    if old and not old[-1][2].endswith("\n") and new_blocks:
        old[-1] = (old[-1][0], old[-1][1], old[-1][2] + "\n")
    out, j = [], 0
    new_blocks = sorted(new_blocks, key=lambda x: x[0])
    for t, _h, txt in old:
        while j < len(new_blocks) and t != float("-inf") and new_blocks[j][0] < t:
            out.append(new_blocks[j][1])
            j += 1
        out.append(txt)
    out.extend(b for _t, b in new_blocks[j:])
    return "".join(out)


def md_header_for(m: dict) -> str:
    try:
        d = datetime.fromisoformat(m["date"]).strftime("%Y-%m-%d %H:%M:%S")
    except (KeyError, ValueError):
        return ""
    return f"[{d}] {m.get('sender') or ''}:"


# ---------------------------------------------------------------- archive map
def load_conv_map(state: Path) -> dict[str, list[tuple[str, str]]]:
    """_state/conv_map.tsv: cid <TAB> NNN folder <TAB> chat subfolder (built by sync_export.py)."""
    cmap: dict = {}
    p = state / "conv_map.tsv"
    if p.exists():
        for line in D.rd(str(p)).splitlines():
            q = line.split("\t")
            if len(q) == 3 and q[0]:
                cmap.setdefault(q[0], []).append((q[1], q[2]))
    return cmap


def resolve(name: str, cid: str | None, cmap: dict, subs: dict, owner_of: dict):
    """-> ('existing', (top, sub)) | ('new', None) | ('skip', reason).
    Conversation id first, then the real subfolder names inside NNN_*/; anything ambiguous is
    skipped, phone-only folders are never touched; only a cid known nowhere becomes a new NNN."""
    if cid and cid in cmap:
        t = cmap[cid]
        if len(t) != 1:
            return "skip", "ambiguous cid"
        target = t[0]
    else:
        cands = subs.get(name, [])
        if len(cands) > 1:
            return "skip", "ambiguous name"
        if len(cands) == 1:
            target = cands[0]
            other = owner_of.get(target)
            if cid and other and other != cid:
                return "skip", "name belongs to another cid"
        elif cid:
            return "new", None
        else:
            return "skip", "no cid and no folder"
    if (owner_of.get(target) or "").startswith("phone:"):
        return "skip", "phone-only folder"
    return "existing", target


def nnn_dirs(out: Path) -> dict[int, Path]:
    res = {}
    for d in out.iterdir() if out.is_dir() else []:
        m = re.match(r"^(\d{3,})_", d.name)
        if m and d.is_dir():
            res[int(m.group(1))] = d
    return res


# ---------------------------------------------------------------- per-chat merge
def plan_chat(src_inner: Path, dst_inner: Path | None) -> dict:
    """Returns {'add': [(ts, line, msg)], 'md': [(ts, block)], 'media': [rel paths]}."""
    new = read_jsonl(src_inner / "data.json")
    old = read_jsonl(dst_inner / "data.json") if dst_inner else []
    add = new_messages([m for _s, m in old], [m for _s, m in new])
    src_md = md_blocks(D.rd(str(src_inner / "chat.md"))) if (src_inner / "chat.md").exists() else []
    pending = [(t, h, txt) for t, h, txt in src_md if h]
    md, media, lines = [], [], []
    for m in add:
        lines.append((ts(m), json.dumps(m, ensure_ascii=False), m))
        head = md_header_for(m)
        for k, (t, h, txt) in enumerate(pending):
            if head and h.startswith(head):
                md.append((t, txt))
                del pending[k]
                break
        for a in m.get("attachments") or []:
            rel = a.get("path") or ""
            if rel and not a.get("missing_kind") and not rel.startswith("../") \
                    and (src_inner / rel).is_file():
                media.append(rel)
    return {"add": lines, "md": md, "media": sorted(set(media)), "old": old}


def apply_chat(root: Path, top: str, inner: str, src_inner: Path, plan: dict, central) -> None:
    dst = root / top / inner
    dst.mkdir(parents=True, exist_ok=True)
    for rel in plan["media"]:
        tgt = dst / rel
        tgt.parent.mkdir(parents=True, exist_ok=True)
        if not tgt.exists():
            shutil.copy2(src_inner / rel, tgt)
    old = plan["old"]
    merged = merge_lines([s for s, _m in old], [ts(m) for _s, m in old],
                         [(t, line) for t, line, _m in plan["add"]])
    D.atomic_write(str(dst / "data.json"), "\n".join(merged) + "\n")
    md_p = dst / "chat.md"
    old_md = D.rd(str(md_p)) if md_p.exists() else ""
    D.atomic_write(str(md_p), md_insert(old_md, plan["md"]))
    if not (dst / "index.html").exists() and (src_inner / "index.html").exists():
        shutil.copy2(src_inner / "index.html", dst / "index.html")
    if plan["media"] and central is not None:
        central.run(top)


def verify_append_only(before: list[str], after_p: Path) -> bool:
    after = [s for s, _m in read_jsonl(after_p)]
    it = iter(after)
    return all(any(b == a for a in it) for b in before)  # before is a subsequence of after


# ---------------------------------------------------------------- source / export
def sigexport_python() -> str:
    exe = os.environ.get("SIGEXPORT") or shutil.which("sigexport") or str(Path.home() / ".local/bin/sigexport")
    try:
        first = Path(exe).read_text(errors="replace").splitlines()[0]
        if first.startswith("#!") and "python" in first:
            return first[2:].strip().split()[0]
    except (OSError, IndexError):
        pass
    raise SystemExit("sigexport (pipx) not found; set SIGEXPORT")


def snapshot(run: Run, quit_signal: bool) -> Path:
    import prepare_source as PS
    from platform_util import find_signal_dir
    sig = Path(os.environ["SIGNAL_DIR"]).expanduser() if os.environ.get("SIGNAL_DIR") else find_signal_dir()
    if sig is None:
        raise SystemExit("Signal Desktop folder not found")
    work = run.out / "_work" / "src"
    if quit_signal:
        was = PS.signal_running()
        try:
            PS.prepare(sig, work, quit_signal=True)
        finally:
            if was and sys.platform == "darwin":
                subprocess.run(["open", "-a", "Signal"], capture_output=True)
    else:
        PS.prepare(sig, work, quit_signal=False)
    return work


def run_export(run: Run, src: Path, tmp: Path, abort_free: float) -> None:
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    py = sigexport_python()
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    with open(run.state / "sync_sigexport.out", "w", encoding="utf-8") as out:
        p = subprocess.Popen([py, str(HERE / "sync_export.py"), "export", str(src), str(tmp), str(run.out)],
                             stdout=out, stderr=subprocess.STDOUT, env=env)
        while True:
            try:
                rc = p.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                if free_gib(run.out) < abort_free:
                    p.terminate()
                    raise SystemExit(f"disk below ABORT_FREE_GIB={abort_free}")
    if rc != 0:
        raise SystemExit(f"sigexport failed rc={rc} (see _state/sync_sigexport.out)")


# ---------------------------------------------------------------- main sync
def sync(run: Run, tmp: Path, upload: bool, remote: str | None) -> int:
    out, state = run.out, run.state
    cmap = load_conv_map(state)
    owner_of = {t: c for c, ts_ in cmap.items() for t in ts_}
    dirs = nnn_dirs(out)
    subs: dict = {}
    for idx_, d in dirs.items():
        for sd in d.iterdir():
            if sd.is_dir() and (sd / "data.json").is_file():
                subs.setdefault(sd.name, []).append((d.name, sd.name))
    used = set(dirs)
    cids = {}
    if (tmp / "_cids.tsv").exists():
        for line in D.rd(str(tmp / "_cids.tsv")).splitlines():
            p = line.split("\t")
            if len(p) == 2:
                cids.setdefault(p[1], p[0])
    chats = sorted(d for d in tmp.iterdir() if d.is_dir() and (d / "data.json").is_file())
    central = None if run.dry else CM.Central(str(out), str(state), str(out / "_work"))
    nxt = max(used | {0}) + 1
    changed, failed = [], []
    for i, src_inner in enumerate(chats):
        run.st["progress"] = round(0.3 + 0.5 * i / max(1, len(chats)), 3)
        name = src_inner.name
        cid = cids.get(name)
        kind, target = resolve(name, cid, cmap, subs, owner_of)
        if kind == "skip":
            run.log(f"skip chat ({target})" + (f" NNN={target[0][:3]}" if isinstance(target, tuple) else ""))
            continue
        is_new = kind == "new"
        if is_new:
            idx, nxt = nxt, nxt + 1
            top = f"{idx:03d}_{safe_component(name)}" if name else f"{idx:03d}_unnamed"
            dst_inner = out / top / name
        else:
            top, sub = target
            idx = int(top[:3])
            dst_inner = out / top / sub
        plan = plan_chat(src_inner, dst_inner if dst_inner.is_dir() else None)
        if not plan["add"]:
            continue
        run.st["added_messages"] += len(plan["add"])
        run.st["new_media"] += len(plan["media"])
        run.st["new_chats"] += int(is_new)
        run.log(f"chat NNN={idx:03d} new={int(is_new)} add={len(plan['add'])} media={len(plan['media'])}")
        if run.dry:
            continue
        backup = out / "_work" / "sync_backup" / top
        before = [s for s, _m in plan["old"]]
        broken_before = linkcheck(out / top) if (out / top).exists() else set()
        try:
            if (out / top).exists():
                shutil.rmtree(backup, ignore_errors=True)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(out / top, backup, copy_function=os.link, symlinks=True)
            apply_chat(out, top, dst_inner.name, src_inner, plan, central)
            if not verify_append_only(before, dst_inner / "data.json"):
                raise RuntimeError("append-only check failed")
            bad = linkcheck(out / top) - broken_before  # only refs this merge broke
            if bad:
                raise RuntimeError(f"link check: {len(bad)} new broken refs")
            if is_new:
                with open(state / "conv_map.tsv", "a", encoding="utf-8") as f:
                    f.write(f"{cid}\t{top}\t{dst_inner.name}\n")
                with open(state / "folders.tsv", "a", encoding="utf-8") as f:
                    f.write(f"{idx}\t{name}\t{dst_inner.name}\n")
                dirs[idx] = out / top
            changed.append(top)
            shutil.rmtree(backup, ignore_errors=True)
        except Exception as e:  # restore the chat exactly as it was
            run.log(f"chat NNN={idx:03d} failed ({type(e).__name__}); restoring backup")
            if backup.exists():
                shutil.rmtree(out / top, ignore_errors=True)
                os.replace(backup, out / top)
            elif is_new:
                shutil.rmtree(out / top, ignore_errors=True)
            failed.append(idx)
        run.write_status()
    run.step("viewer", 0.85)
    if not run.dry and changed:
        r = subprocess.run([sys.executable, str(HERE.parent / "viewer" / "build_viewer.py"), "--root", str(out),
                            "--out", str(out / "viewer") if (out / "viewer").is_dir() else str(HERE.parent / "viewer")],
                           capture_output=True, text=True)
        if r.returncode != 0:
            run.log("viewer rebuild failed")
            failed.append(-1)
    if upload and not run.dry:
        run.step("upload", 0.9)
        if not remote:
            raise SystemExit("--upload needs REMOTE")
        ex = [a for e in load_upload_excludes() for a in ("--exclude", e)]
        rc = subprocess.run(["rclone", "copy", str(out), remote, *ex, "--transfers", "4", "--checkers", "8",
                             "--retries", "5", "--log-file", str(state / "upload.log"), "--log-level", "INFO"]).returncode
        with open(state / "upload_check.log", "w") as f:
            crc = subprocess.run(["rclone", "check", str(out), remote, "--one-way", *ex, "--checkers", "8"],
                                 stdout=f, stderr=subprocess.STDOUT).returncode
        run.log(f"upload rc={rc} check rc={crc}")
        if rc or crc:
            failed.append(-2)
        else:
            run.upload_ok = True
    return 1 if failed else 0


# ---------------------------------------------------------------- prune Signal Desktop
def signal_dir() -> Path:
    from platform_util import find_signal_dir
    sig = Path(os.environ["SIGNAL_DIR"]).expanduser() if os.environ.get("SIGNAL_DIR") else find_signal_dir()
    if sig is None:
        raise SystemExit("Signal Desktop folder not found")
    return sig


def run_prune(run: Run, days: int, remote: str, dry: bool) -> dict:
    """Fresh read-only sql copy (Signal may run), then prune_signal.py under the sigexport python."""
    import prepare_source as PS
    sig = signal_dir()
    work = run.out / "_work" / "prune_src"
    shutil.rmtree(work, ignore_errors=True)
    (work).mkdir(parents=True, exist_ok=True)
    PS.copy_sql_live(sig / "sql", work / "sql")
    cmd = [sigexport_python(), str(HERE / "prune_signal.py"), "--sql-src", str(work), "--signal-dir", str(sig),
           "--out", str(run.out), "--days", str(days), "--remote", remote] + (["--dry-run"] if dry else [])
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    shutil.rmtree(work, ignore_errors=True)
    for line in r.stdout.splitlines():
        if line.startswith("PRUNE_SUMMARY "):
            return json.loads(line[len("PRUNE_SUMMARY "):])
    raise RuntimeError(f"prune_signal rc={r.returncode}")


def maybe_prune(run: Run, days: int, upload: bool, sync_rc: int, remote: str | None, dry: bool = False,
                runner=run_prune):
    """Real prune only after a successful sync AND upload check in this run. Returns the summary or None."""
    if not days or days < 1:
        return None
    if not dry and not (upload and sync_rc == 0 and run.upload_ok and not run.dry):
        run.log("prune skipped: needs --upload with a successful sync and rclone check in this run")
        return None
    if not remote:
        run.log("prune skipped: no REMOTE")
        return None
    run.step("prune", 0.95)
    try:
        s = runner(run, days, remote, dry)
    except Exception as ex:  # noqa: BLE001  prune never fails the sync
        run.log(f"prune failed ({type(ex).__name__}); nothing deleted")
        return None
    run.st["pruned_files"], run.st["pruned_bytes"] = int(s.get("pruned_files", 0)), int(s.get("pruned_bytes", 0))
    if dry:
        run.st["prune_dry_run"] = True
    run.log(f"prune{' dry-run' if dry else ''} days={days} candidates={s.get('candidates', 0)} "
            f"files={run.st['pruned_files']} bytes={run.st['pruned_bytes']} "
            f"kept={json.dumps(s.get('kept', {}), sort_keys=True)}")
    run.write_status()
    return s


def drop_staging(s) -> None:
    st = (s or {}).get("staging")
    if st and "_work" in Path(st).parts and "pruned" in Path(st).parts:
        shutil.rmtree(st, ignore_errors=True)


def linkcheck(chat: Path) -> set:
    """Set of (data.json, ref) for refs to Media/ or media/ in the chat that do not resolve,
    skipping the ones flagged missing by sigexport. Callers diff it against the set taken
    before the merge, so refs that were already broken never fail a sync."""
    bad = set()
    for p in chat.rglob("data.json"):
        for _s, m in read_jsonl(p):
            for a in m.get("attachments") or []:
                rel = a.get("path") or ""
                if rel and not a.get("missing_kind") and ("Media/" in rel or rel.startswith("media/")):
                    if not (p.parent / rel).exists():
                        bad.add((str(p.relative_to(chat)), rel))
    return bad


def main(argv=None) -> int:
    e = os.environ.get
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=e("OUT_DIR", str(Path.home() / "SignalExport")))
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quit-signal", action="store_true")
    ap.add_argument("--setup-key", action="store_true", help="one-time: store the DB key in the login Keychain")
    ap.add_argument("--from-export", help=argparse.SUPPRESS)  # tests: use an existing sigexport dir
    ap.add_argument("--min-free", type=float, default=float(e("MIN_FREE_GIB", "8")))
    ap.add_argument("--abort-free", type=float, default=float(e("ABORT_FREE_GIB", "3")))
    ap.add_argument("--prune-signal-days", type=int, default=int(e("PRUNE_SIGNAL_DAYS", "0") or 0),
                    help="after a successful sync + upload check, delete Signal Desktop attachment files "
                         "older than N days that are verified in the archive and on the remote (0 = off)")
    ap.add_argument("--prune-dry-run", action="store_true",
                    help="only report what the prune would delete (read-only, no sync, no upload)")
    a = ap.parse_args(argv)
    out = Path(a.out).expanduser().resolve()
    if a.setup_key:
        from platform_util import find_signal_dir
        sig = Path(e("SIGNAL_DIR")).expanduser() if e("SIGNAL_DIR") else find_signal_dir()
        return subprocess.run([sigexport_python(), str(HERE / "sync_export.py"), "setup-key", str(sig)]).returncode
    lock = out / "_state" / "sync.lock"
    try:
        acquire_lock(lock)
    except Busy:
        print("sync already running", file=sys.stderr)
        return EX_BUSY
    run = Run(out, a.dry_run)
    pruned = None
    try:
        if a.prune_dry_run:
            run.step("prune", 0.5)
            pruned = maybe_prune(run, a.prune_signal_days or 30, False, 0, e("REMOTE"), dry=True)
            run.end(pruned is not None, "prune dry run" + ("" if pruned is not None else " failed, see sync.log"))
            return 0 if pruned is not None else 1
        run.step("disk", 0.02)
        if free_gib(out) < a.min_free:
            raise SystemExit(f"free space below MIN_FREE_GIB={a.min_free}")
        if a.from_export:
            tmp = Path(a.from_export)
        else:
            run.step("snapshot", 0.05)
            src = snapshot(run, a.quit_signal)
            run.step("export", 0.1)
            tmp = out / "_work" / "sync_tmp"
            run_export(run, src, tmp, a.abort_free)
        run.step("merge", 0.3)
        rc = sync(run, tmp, a.upload, e("REMOTE"))
        pruned = maybe_prune(run, a.prune_signal_days, a.upload, rc, e("REMOTE"))
        run.end(rc == 0, ("dry run: " if a.dry_run else "") + ("ok" if rc == 0 else "some chats failed, see sync.log"))
        return rc
    except SystemExit as ex:
        run.end(False, str(ex.code))
        return 1
    except Exception as ex:  # noqa: BLE001
        run.end(False, type(ex).__name__)
        run.log(f"error {type(ex).__name__}")
        return 1
    finally:
        drop_staging(pruned)  # after the run summary (sync_status.json + sync.log) is written
        try:
            if lock.read_text().strip() == str(os.getpid()):
                lock.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
