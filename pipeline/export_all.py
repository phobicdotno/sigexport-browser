#!/usr/bin/env python3
"""sigexport-browser pipeline (macOS, Linux, Windows; stdlib only, Python 3.10+).

Exports every Signal Desktop chat with sigexport, one chat per run, deduplicates media
across chats (md5, first copy wins, links rewritten with forward slashes), verifies links,
optionally copies each chat to an rclone remote and verifies with `rclone check --one-way`.
Local exports are never deleted after success.

Configuration (env vars or flags; flags win):
  SIGNAL_SRC      Signal dir or prepared copy (default: OUT_DIR/_work/src if prepared, else auto-detect) --src DIR
  OUT_DIR         export destination (default ~/SignalExport)           --out DIR
  REMOTE          rclone destination, e.g. myremote:Signal-export       --remote R
                  (empty = no upload, no check)
  MEDIA_LAYOUT    central (default): every unique file once in          --media-layout L
                  OUT_DIR/Media/<Type>/, chats link ../../Media/<Type>/<file>;
                  per-chat: old layout, media/ per chat, cross-chat dedup links
  MIN_FREE_GIB    a chat with no size estimate starts only with this      --min-free N
                  much free (8); with an estimate: estimate*1.15 + ABORT_FREE_GIB
  ABORT_FREE_GIB  hard floor: sigexport is stopped (terminate, then kill) --abort-free N
                  and the chat is marked for retry when free space drops below (3)
  POLL_SECS       free-space poll interval during sigexport (1.5)
  DISK_WAIT_SECS  wait this long for space before skipping a chat that won't fit (300)
  TRANSFERS       rclone --transfers (2)                                --transfers N
  SIGEXPORT       sigexport binary (default: found on PATH)
  STATE_DIR       logs/state (default $OUT_DIR/_state; never /tmp)
  CHAT_TIMEOUT    seconds per chat export (14400); BLOCK_SECS (600): no output dir after
                  this long = key prompt (macOS Keychain) waiting -> stop with exit 3
  UPLOAD_TIMEOUT  rclone copy seconds (21600); CHECK_TIMEOUT rclone check seconds (7200)

Resumable: re-run and it skips chat indexes listed in done.txt / failed.txt. Chats skipped or
stopped for disk space go to retry.tsv (not failed.txt) and are tried again on the next run.
A chat interrupted mid-centralisation (journal in central_journal/) is finished or rolled
back from its hard-link backup (OUT_DIR/_work/backup) before anything else.
Before exporting, existing NNN_x folders must match the regenerated chats.txt order,
otherwise the run aborts (exit 65) without touching anything.
Size estimate per chat: chat_bytes.tsv (raw bytes from an earlier export/abort), else
hashes.tsv (per-NNN sizes from a central migration), else MIN_FREE_GIB.
State files in STATE_DIR: chats.txt (unique names, index = line number), done.txt,
failed.txt, manifest.csv, media_index.tsv, folders.tsv (idx -> chat folders that export
produced), collisions.tsv (name matched >1 chat, folder already exported by another index,
duplicate or unselectable names), export_all.log.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from platform_util import (KeepAwake, dir_size, find_signal_dir, free_gib,  # noqa: E402
                           safe_component, windows_problem)
import central_media as CM  # noqa: E402

DD = HERE / "dedup.py"
MAN_HEADER = ("index,chat_name,media_files,deduped,bytes_saved,bytes_kept,upload,check,"
              "linkcheck,free_gib_after,missing_reported")
def child_env(**extra) -> dict:
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **extra}


def csvq(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def read_lines(p: Path) -> list[str]:
    if not p.exists():
        return []
    return p.read_text(encoding="utf-8").splitlines()


def append(p: Path, line: str) -> None:
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")


def idx_set(p: Path) -> set[int]:
    out = set()
    for line in read_lines(p):
        head = line.split("\t", 1)[0].strip()
        if head.isdigit():
            out.add(int(head))
    return out


def chat_dir_name(idx: int, name: str) -> str:
    return f"{idx:03d}_{safe_component(name)}"


def parse_list_chats(text: str) -> list[str]:
    """sigexport --list-chats prints sorted display names joined by ' | ' (main.py)."""
    s = text.rstrip("\n")
    return [n for n in s.split(" | ")] if s else []


def unique_names(names: list[str]) -> tuple[list[str], list[str]]:
    seen, out, dups = set(), [], []
    for n in names:
        if n in seen:
            dups.append(n)
            continue
        seen.add(n)
        out.append(n)
    return out, dups


def produced_folders(w: Path) -> list[str]:
    if not w.is_dir():
        return []
    return sorted(d.name for d in w.iterdir() if (d / "chat.md").is_file())


def load_folders(p: Path) -> dict[int, list[str]]:
    out = {}
    for line in read_lines(p):
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].isdigit():
            out[int(parts[0])] = [f for f in parts[2].split("|") if f]
    return out


def find_collisions(idx: int, folders: list[str], earlier: dict[int, list[str]]) -> list[tuple[str, str]]:
    """Collisions for one export: --chats matches name OR profileName OR nickname
    (sigexport data.py fetch_data), so one name can pull in several conversations, and two
    names can select the same conversation. sigexport's fix_names() gives each conversation
    a deterministic unique folder, so the same folder under two indexes = same chat twice."""
    out = []
    if len(folders) > 1:
        out.append(("multi-match", f"{len(folders)} chats: " + "|".join(folders)))
    for f in folders:
        for j, fs in sorted(earlier.items()):
            if j != idx and f in fs:
                out.append(("same-folder", f"{f} also exported by index {j}"))
        prob = windows_problem(f)
        if prob:
            out.append(("windows-name", f"{f}: {prob}"))
    return out


class Pipeline:
    def __init__(self, a):
        self.src = Path(a.src).expanduser()
        self.root = Path(a.out).expanduser()
        self.remote = a.remote or ""
        self.min_free = float(a.min_free)
        self.abort_free = float(a.abort_free)
        self.transfers = str(a.transfers)
        self.sig = a.sigexport
        self.state = Path(a.state).expanduser() if a.state else self.root / "_state"
        self.layout = getattr(a, "media_layout", "central") or "central"
        self.poll = float(os.environ.get("POLL_SECS", "1.5"))
        self.disk_wait = float(os.environ.get("DISK_WAIT_SECS", "300"))
        self.chat_timeout = float(os.environ.get("CHAT_TIMEOUT", "14400"))
        self.block_secs = float(os.environ.get("BLOCK_SECS", "600"))
        self.upload_timeout = float(os.environ.get("UPLOAD_TIMEOUT", "21600"))
        self.check_timeout = float(os.environ.get("CHECK_TIMEOUT", "7200"))
        s = self.state
        self.log_p, self.chats_p = s / "export_all.log", s / "chats.txt"
        self.done_p, self.failed_p = s / "done.txt", s / "failed.txt"
        self.man_p, self.idx_p = s / "manifest.csv", s / "media_index.tsv"
        self.stk_p, self.sigout_p = s / "stickers_done", s / "sigexport_last.out"
        self.folders_p, self.coll_p = s / "folders.tsv", s / "collisions.tsv"
        self.retry_p, self.bytes_p = s / "retry.tsv", s / "chat_bytes.tsv"
        self.central = CM.Central(str(self.root), str(self.state), str(self.root / "_work"))

    # -- helpers
    def log(self, msg: str) -> None:
        append(self.log_p, f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}")

    def gib(self) -> int:
        return int(free_gib(self.root))

    def mark(self, idx, name, media, ded, saved, kept, up, ck, lc, mrep=0):
        append(self.man_p, f"{idx},{csvq(name)},{media},{ded},{saved},{kept},{up},{ck},{lc},{self.gib()},{mrep}")

    def run_log(self, cmd, timeout):
        with open(self.log_p, "a", encoding="utf-8") as lf:
            try:
                return subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout,
                                      env=child_env()).returncode
            except subprocess.TimeoutExpired:
                lf.write(f"timeout after {timeout}s: {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}\n")
                return 124

    def dd(self, mode: str, d: str) -> tuple[int, dict]:
        import json
        env = child_env(LC_SIGLOG=str(self.sigout_p))
        with open(self.log_p, "a", encoding="utf-8") as lf:
            p = subprocess.run([sys.executable, "-I", str(DD), mode, str(self.root), d, str(self.idx_p)],
                               stdout=subprocess.PIPE, stderr=lf, env=env, encoding="utf-8")
        try:
            res = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
        except ValueError:
            res = {}
        return p.returncode, res

    def upload_manifests(self):
        if not self.remote:
            return
        self.run_log(["rclone", "copyto", str(self.man_p), f"{self.remote}/_manifest/manifest.csv"], 600)
        self.run_log(["rclone", "copyto", str(self.idx_p), f"{self.remote}/_manifest/media_index.tsv"], 600)
        self.log("manifests uploaded")

    # -- disk guard
    def estimate_bytes(self, idx: int) -> int | None:
        """Raw attachment bytes of chat idx from an earlier run. sigexport and the (encrypted)
        DB expose no sizes without decrypting it, so: chat_bytes.tsv (written after each export or
        abort, max wins), else hashes.tsv from a central migration (relpath starts with NNN_)."""
        best = None
        for line in read_lines(self.bytes_p):
            p = line.split("\t")
            if len(p) >= 2 and p[0].isdigit() and int(p[0]) == idx and p[1].isdigit():
                best = max(best or 0, int(p[1]))
        if best is not None:
            return best
        hp = self.state / "hashes.tsv"
        if hp.exists():
            pre, tot, hit = f"{idx:03d}_", 0, False
            for line in read_lines(hp):
                p = line.split("\t")
                if len(p) == 3 and p[0].startswith(pre) and p[2].isdigit():
                    tot += int(p[2])
                    hit = True
            if hit:
                return tot
        return None

    def need_gib(self, idx: int) -> tuple[float, str]:
        est = self.estimate_bytes(idx)
        if est is None:
            return self.min_free, "no estimate"
        return max(self.abort_free + 1, est * 1.15 / (1 << 30) + self.abort_free), f"est_gib={est / (1 << 30):.2f}"

    def wait_disk(self, idx: int) -> bool:
        """True when the chat fits; waits up to DISK_WAIT_SECS, else False (skip, retry later)."""
        need, why = self.need_gib(idx)
        t0 = time.monotonic()
        logged = False
        while free_gib(self.root) < need:
            if time.monotonic() - t0 >= self.disk_wait:
                return False
            if not logged:
                self.log(f"chat={idx} paused: free_gib={self.gib()} need_gib={need:.1f} ({why})")
                logged = True
            time.sleep(min(30.0, max(0.01, self.disk_wait)))
        return True

    def record_bytes(self, idx: int, w: Path) -> None:
        if w.is_dir():
            append(self.bytes_p, f"{idx}\t{dir_size(w)}")

    def stop(self, p) -> None:
        """Stop sigexport cleanly: terminate, give it 10 s, then kill."""
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()

    # -- export one chat: 0 ok, 1 fail, 2 low-disk abort, 3 blocked
    def export_chat(self, name: str, w: Path) -> int:
        cmd = [self.sig, "--source", str(self.src), "--chats", name]
        if self.stk_p.exists():
            cmd.append("--no-stickers")  # stickers are exported once (first chat)
        cmd.append(str(w))
        shutil.rmtree(w, ignore_errors=True)
        with open(self.sigout_p, "w", encoding="utf-8") as out:
            p = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, env=child_env())
            t0 = time.monotonic()
            while True:
                try:
                    rc = p.wait(timeout=self.poll)
                    return 0 if rc == 0 else 1
                except subprocess.TimeoutExpired:
                    pass
                waited = time.monotonic() - t0
                code = None
                if free_gib(self.root) < self.abort_free:
                    code = 2
                elif waited >= self.block_secs and not w.exists():
                    code = 3  # nothing written: almost always the key/Keychain prompt waiting
                elif waited >= self.chat_timeout:
                    code = 1
                if code is not None:
                    self.stop(p)
                    return code

    def process(self, idx: int, name: str) -> None:
        d = chat_dir_name(idx, name)
        w = self.root / d
        if not self.wait_disk(idx):
            need, why = self.need_gib(idx)
            self.log(f"chat={idx} SKIPPED low disk: free_gib={self.gib()} need_gib={need:.1f} ({why}) -> retry.tsv")
            print(f"skipped chat {idx}: needs ~{need:.1f} GiB free, have {free_gib(self.root):.1f} (see retry.tsv)",
                  file=sys.stderr)
            append(self.retry_p, f"{idx}\t{name}\tskipped-lowdisk need_gib={need:.1f}")
            self.mark(idx, name, 0, 0, 0, 0, "skipped-lowdisk", "-", "-")
            return
        self.log(f"chat={idx} start dir={d} free_gib={self.gib()}")
        (self.state / "present" / f"{d}.json").unlink(missing_ok=True)
        t0 = time.time()
        rc = self.export_chat(name, w)
        if rc == 3:
            self.log(f"chat={idx} BLOCKED >{int(self.block_secs)}s (key prompt? on macOS choose Allow) -> stopping")
            append(self.failed_p, f"{idx}\t{name}\texport blocked")
            self.mark(idx, name, 0, 0, 0, 0, "-", "-", "-")
            raise SystemExit(3)
        if rc == 2:
            self.log(f"chat={idx} free disk <{self.abort_free}GiB mid-export: sigexport stopped, "
                     "removing this chat's partial folder only -> retry.tsv")
            print(f"stopped chat {idx}: free space fell below {self.abort_free} GiB (see retry.tsv)", file=sys.stderr)
            self.record_bytes(idx, w)  # lower bound for next run's estimate
            shutil.rmtree(w, ignore_errors=True)
            append(self.retry_p, f"{idx}\t{name}\tlow disk abort")
            self.mark(idx, name, 0, 0, 0, 0, "aborted-lowdisk", "-", "-")
            return
        n_media = sum(1 for p in w.glob("*/media/*") if p.is_file()) if w.is_dir() else 0
        if rc == 0:
            self.record_bytes(idx, w)
        self.log(f"chat={idx} export rc={rc} secs={int(time.time() - t0)} media_files={n_media}")
        folders = produced_folders(w)
        if rc == 0 and not folders:
            self.log(f"chat={idx} empty export")
            shutil.rmtree(w, ignore_errors=True)
            append(self.done_p, f"{idx}\t{name}\tempty")
            self.mark(idx, name, 0, 0, 0, 0, "empty", "-", "-")
            return
        if rc != 0:
            self.log(f"chat={idx} FAILED export")
            shutil.rmtree(w, ignore_errors=True)
            append(self.failed_p, f"{idx}\t{name}\texport failed")
            self.mark(idx, name, 0, 0, 0, 0, "failed-export", "-", "-")
            return
        self.stk_p.touch()
        earlier = load_folders(self.folders_p)
        append(self.folders_p, f"{idx}\t{name}\t{'|'.join(folders)}")
        for kind, detail in find_collisions(idx, folders, earlier):
            append(self.coll_p, f"{idx}\t{kind}\t{detail}")
            self.log(f"chat={idx} COLLISION {kind}")
        if self.layout == "central":
            r = self.centralise(idx, name, d)
            if r is None:
                return
            nded, saved, kept, lcstat, mrep = r
        else:
            nded, saved, kept, lcstat, mrep = self.dedup_per_chat(idx, name, d, w, n_media)
            if lcstat is None:
                return
        self.finish_chat(idx, name, d, w, n_media, nded, saved, kept, lcstat, mrep)

    def centralise(self, idx, name, d):
        try:
            res = self.central.run(d)
        except CM.CentralError as ex:
            self.log(f"chat={idx} CENTRAL FAILED ({ex})")
            append(self.failed_p, f"{idx}\t{name}\tcentral failed")
            self.mark(idx, name, 0, 0, 0, 0, "-", "-", "central_failed")
            return None
        self.log(f"chat={idx} central {json.dumps(res)}")
        return res["deduped"], res["saved"], res["kept"], "ok", 0

    def dedup_per_chat(self, idx, name, d, w, n_media):
        # dedup + link check (sigexport-reported missing attachments are not failures)
        lcstat = "ok"
        drc, res = self.dd("dedup", d)
        if drc != 0 or not res:
            lcstat, res = "link_failed", {}
        lrc, lc = self.dd("linkcheck", d)
        if lrc != 0 or lcstat != "ok":
            self.log(f"chat={idx} LINK_FAILED ({lc}) -> re-export without dedup")
            lcstat = "link_failed"
            if self.export_chat(name, w) != 0:
                shutil.rmtree(w, ignore_errors=True)
                append(self.failed_p, f"{idx}\t{name}\tre-export failed")
                self.mark(idx, name, n_media, 0, 0, 0, "failed-export", "-", "link_failed")
                return 0, 0, 0, None, 0
            _, res = self.dd("indexonly", d)
            lrc, lc = self.dd("linkcheck", d)
            if lrc != 0:
                lcstat = "link_failed_nodedup_still_bad"
        nded, saved, kept = res.get("deduped", 0), res.get("saved", 0), res.get("kept", 0)
        mrep = lc.get("missing_reported", 0) if lc else 0
        self.log(f"chat={idx} dedup media={n_media} linked={nded} bytes_saved={saved} "
                 f"bytes_kept={kept} linkcheck={lcstat} {lc}")
        return nded, saved, kept, lcstat, mrep

    def finish_chat(self, idx, name, d, w, n_media, nded, saved, kept, lcstat, mrep):
        remote = f"{self.remote}/{d}"
        up = ck = "skipped"
        if self.remote:
            mib = dir_size(w) // (1 << 20)
            u0 = time.time()
            rc = self.run_log(["rclone", "copy", str(w), remote, "--transfers", self.transfers,
                               "--checkers", "8", "--retries", "8", "--low-level-retries", "20",
                               "--retries-sleep", "10s"], self.upload_timeout)
            self.log(f"chat={idx} upload rc={rc} secs={max(1, int(time.time() - u0))} size_mib={mib}")
            if rc != 0:
                up = "failed"
                append(self.failed_p, f"{idx}\t{name}\tupload rc={rc}")
            else:
                up = ck = "ok"
                if self.run_log(["rclone", "check", str(w), remote, "--one-way", "--checkers", "8"],
                                self.check_timeout) != 0:
                    ck = "failed"
                    append(self.failed_p, f"{idx}\t{name}\tcheck failed")
                    self.log(f"chat={idx} CHECK FAILED")
                else:
                    self.log(f"chat={idx} check OK (0 differences)")
        if up != "failed" and ck != "failed":
            append(self.done_p, f"{idx}\t{name}")
        self.mark(idx, name, n_media, nded, saved, kept, up, ck, lcstat, mrep)
        self.log(f"chat={idx} finished free_gib={self.gib()}")

    def ensure_chats(self) -> list[str]:
        if not self.chats_p.exists() or self.chats_p.stat().st_size == 0:
            p = subprocess.run([self.sig, "--source", str(self.src), "--list-chats"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=child_env(), encoding="utf-8", errors="replace")
            names, dups = unique_names(parse_list_chats(p.stdout))
            for n in dups:
                append(self.coll_p, f"0\tduplicate-name\t{n}: one --chats run exports every match")
            self.chats_p.write_text("".join(n + "\n" for n in names), encoding="utf-8", newline="\n")
        return read_lines(self.chats_p)

    def run(self) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        for p in (self.done_p, self.failed_p, self.idx_p):
            p.touch()
        if not self.man_p.exists():
            self.man_p.write_text(MAN_HEADER + "\n", encoding="utf-8", newline="\n")
        for top in self.central.pending():  # crash mid-centralisation: finish or roll back
            try:
                self.log(f"resume central {top[:3]} {json.dumps(self.central.resume(top))}")
            except CM.CentralError as ex:
                self.log(f"resume central {top[:3]} FAILED ({ex}); will re-export")
                shutil.rmtree(self.root / top, ignore_errors=True)
        names = self.ensure_chats()
        bad = CM.verify_order(str(self.root), names)
        if bad:
            msg = (f"ABORT: chats.txt order does not match existing folders (mismatched NNN: "
                   f"{' '.join(bad)}). Nothing exported. Fix or remove {self.chats_p} and rerun.")
            self.log(msg)
            print(msg, file=sys.stderr)
            return 65
        self.log(f"START total_chats={len(names)} free_gib={self.gib()} remote={self.remote or 'none'}")
        done, failed = idx_set(self.done_p), idx_set(self.failed_p)
        first_idx: dict[str, int] = {}
        cnt = 0
        with KeepAwake():
            for idx, name in enumerate(names, 1):
                if name in first_idx:  # older chats.txt with duplicates: same selection twice
                    if idx not in done and idx not in failed:
                        append(self.coll_p, f"{idx}\tduplicate-name\tsame name as index {first_idx[name]}")
                        append(self.done_p, f"{idx}\t{name}\tduplicate-name")
                    continue
                first_idx[name] = idx
                if idx in done or idx in failed:
                    continue
                if "," in name:  # sigexport splits --chats on ',' and would select other chats
                    append(self.coll_p, f"{idx}\tunselectable\tname contains ','")
                    append(self.failed_p, f"{idx}\t{name}\tname contains comma")
                    continue
                self.process(idx, name)
                cnt += 1
                if cnt % 10 == 0:
                    self.upload_manifests()
        self.log(f"FINISHED done={len(read_lines(self.done_p))} failed={len(read_lines(self.failed_p))}")
        self.upload_manifests()
        ncoll = len(read_lines(self.coll_p))
        print(f"Finished. Log: {self.log_p}  Manifest: {self.man_p}"
              + (f"  Collisions: {ncoll} (see {self.coll_p})" if ncoll else ""))
        return 0


def default_sigexport() -> str | None:
    env = os.environ.get("SIGEXPORT")
    if env:
        return env
    found = shutil.which("sigexport")
    if found:
        return found
    exe = "sigexport.exe" if sys.platform == "win32" else "sigexport"
    for c in (Path.home() / ".local" / "bin" / exe, Path("/opt/homebrew/bin") / exe,
              Path("/usr/local/bin") / exe):
        if c.is_file():
            return str(c)
    return None


def main(argv=None) -> int:
    e = os.environ.get
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=e("SIGNAL_SRC") or None)
    ap.add_argument("--out", default=e("OUT_DIR") or str(Path.home() / "SignalExport"))
    ap.add_argument("--remote", default=e("REMOTE", ""))
    ap.add_argument("--media-layout", choices=("central", "per-chat"), default=e("MEDIA_LAYOUT") or "central")
    ap.add_argument("--min-free", default=e("MIN_FREE_GIB", "8"))
    ap.add_argument("--abort-free", default=e("ABORT_FREE_GIB", "3"))
    ap.add_argument("--transfers", default=e("TRANSFERS", "2"))
    ap.add_argument("--state", default=e("STATE_DIR") or None)
    ap.add_argument("--sigexport", default=None)
    a = ap.parse_args(argv)
    prepared = Path(a.out).expanduser() / "_work" / "src"
    if not a.src and (prepared / "config.json").is_file():
        a.src = str(prepared)  # copy made by prepare_source.py
    if not a.src:
        found = find_signal_dir()
        if found:
            a.src = str(found)
            print(f"Using detected Signal folder: {found} (run prepare_source.py for a safer copy)")
    if not a.src or not (Path(a.src).expanduser() / "config.json").is_file():
        print("SIGNAL_SRC/--src must point to a Signal folder or a dir prepared by prepare_source.py",
              file=sys.stderr)
        return 64
    a.sigexport = a.sigexport or default_sigexport()
    if not a.sigexport:
        print("sigexport not found (pipx install signal-export==3.9.3)", file=sys.stderr)
        return 64
    if a.remote and not shutil.which("rclone"):
        print("REMOTE set but rclone not installed", file=sys.stderr)
        return 64
    return Pipeline(a).run()


if __name__ == "__main__":
    sys.exit(main())
