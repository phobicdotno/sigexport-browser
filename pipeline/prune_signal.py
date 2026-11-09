#!/usr/bin/env python3
"""Prune old Signal Desktop message attachments that are verified safe in the archive and remote.

Runs under the sigexport (pipx) interpreter (needs sqlcipher3 + pycryptodome), started by
sync.py only after a sync AND its `rclone check --one-way` both succeeded in the same run.

  prune_signal.py --sql-src DIR --signal-dir SIG --out OUT --days N --remote R [--dry-run]

Candidates: files under SIG/attachments.noindex/ referenced ONLY by message_attachments rows of
attachmentType 'attachment' whose message (max of sentAt / receivedAtMs) is older than N days.
A path also used by a newer row, another attachment type, a thumbnail/screenshot column, or a
row without a localKey is never touched. Stickers, avatars, drafts, temp, downloads and badges
live in other folders and are never looked at.

Per file, all required (else the file is kept and the reason counted):
  1. decrypts in memory with its localKey (HMAC-SHA256 checked, AES-256-CBC, as sigexport
     files.decrypt_attachment), sha256 matches plaintextHash when one is stored
  2. plaintext md5 is in OUT/_state/media_index.tsv and that archive file exists with that md5
  3. the same file exists on the remote with the same md5 (one `rclone md5sum REMOTE/Media`)

Verified files are moved to OUT/_work/pruned/<date>/ (sync.py deletes that folder after the run
summary is written). Prints one JSON summary line; never prints file names.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DAY_MS = 86400 * 1000
CIPHER_KEY_SIZE = 32
MAC_KEY_SIZE = 32
IV_SIZE = 16
MAC_SIZE = 32
SAFE_REL = re.compile(r"^[0-9A-Za-z_-]{1,8}/[0-9A-Za-z_.-]{8,}$")

# Paths referenced in any of these columns of any row are "in use" by that row.
PATH_COLS = ("path", "thumbnailPath", "screenshotPath", "backupThumbnailPath")


def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    try:
        from Crypto.Cipher import AES  # pycryptodome (sigexport dependency)
        return AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    except ImportError:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        return d.update(data) + d.finalize()


def decrypt(blob: bytes, local_key_b64: str, size: int) -> bytes:
    """Same steps as sigexport 3.9.3 files.decrypt_attachment, in memory."""
    keys = base64.b64decode(local_key_b64)
    if len(keys) != CIPHER_KEY_SIZE + MAC_KEY_SIZE:
        raise ValueError("bad key length")
    cipher_key, mac_key = keys[:CIPHER_KEY_SIZE], keys[CIPHER_KEY_SIZE:]
    if len(blob) < IV_SIZE + MAC_SIZE:
        raise ValueError("too short")
    iv, their_mac, body = blob[:IV_SIZE], blob[-MAC_SIZE:], blob[IV_SIZE:-MAC_SIZE]
    if len(body) % 16:
        raise ValueError("bad length")
    if not hmac.compare_digest(hmac.new(mac_key, iv + body, hashlib.sha256).digest(), their_mac):
        raise ValueError("mac mismatch")
    plain = _aes_cbc_decrypt(cipher_key, iv, body)
    if len(plain) < int(size):
        raise ValueError("short plaintext")
    return plain[: int(size)]


def find_candidates(cur, cutoff_ms: int) -> list[dict]:
    """Paths whose every reference is an old 'attachment' row with a localKey."""
    cols = {r[1] for r in cur.execute("PRAGMA table_info(message_attachments)").fetchall()}
    age = "MAX(COALESCE(sentAt,0), COALESCE(receivedAtMs,0))" if "receivedAtMs" in cols else "COALESCE(sentAt,0)"
    rows = cur.execute(
        f"SELECT path, localKey, size, plaintextHash, {age} FROM message_attachments "
        "WHERE attachmentType = 'attachment' AND path IS NOT NULL AND path != ''").fetchall()
    used_elsewhere: set[str] = set()
    for col in PATH_COLS:
        if col not in cols:
            continue
        q = f"SELECT {col} FROM message_attachments WHERE {col} IS NOT NULL AND {col} != ''"
        if col == "path":
            q += f" AND (attachmentType != 'attachment' OR {age} >= ? OR localKey IS NULL OR localKey = '')"
            used_elsewhere.update(r[0] for r in cur.execute(q, (cutoff_ms,)).fetchall())
        else:
            used_elsewhere.update(r[0] for r in cur.execute(q).fetchall())
    by_path: dict[str, dict] = {}
    for path, key, size, ph, t in rows:
        if path in used_elsewhere or t >= cutoff_ms or not key:
            continue
        prev = by_path.get(path)
        if prev and (prev["localKey"] != key or prev["size"] != size):
            used_elsewhere.add(path)  # inconsistent references: keep
            continue
        by_path[path] = {"path": path, "localKey": key, "size": int(size), "plaintextHash": ph}
    return [c for p, c in sorted(by_path.items()) if p not in used_elsewhere]


def load_index(out: Path) -> dict[str, str]:
    idx: dict[str, str] = {}
    p = out / "_state" / "media_index.tsv"
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[0]:
                idx.setdefault(parts[0], parts[1])
    return idx


def file_md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def remote_media_md5s(remote: str) -> dict[str, str]:
    """{'Media/<Type>/<file>': md5} from one `rclone md5sum REMOTE/Media` listing."""
    r = subprocess.run(["rclone", "md5sum", remote.rstrip("/") + "/Media", "--checkers", "8"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"rclone md5sum rc={r.returncode}")
    res = {}
    for line in r.stdout.splitlines():
        m = re.match(r"^([0-9a-f]{32})  (.+)$", line)
        if m:
            res["Media/" + m.group(2)] = m.group(1)
    return res


def verify(c: dict, att_dir: Path, out: Path, index: dict, remote: dict, md5_cache: dict):
    """-> (ok, reason, nbytes). Reads the Signal file once, never writes."""
    rel = c["path"]
    if not SAFE_REL.match(rel) or ".." in rel:
        return False, "odd_path", 0
    f = att_dir / rel
    if f.is_symlink() or not f.is_file():
        return False, "missing_in_signal", 0
    try:
        if f.resolve().parent.parent != att_dir.resolve():
            return False, "odd_path", 0
        blob = f.read_bytes()
    except OSError:
        return False, "unreadable", 0
    try:
        plain = decrypt(blob, c["localKey"], c["size"])
    except Exception:  # noqa: BLE001
        return False, "decrypt_failed", len(blob)
    ph = (c.get("plaintextHash") or "").lower()
    if re.fullmatch(r"[0-9a-f]{64}", ph) and hashlib.sha256(plain).hexdigest() != ph:
        return False, "sha256_mismatch", len(blob)
    md5 = hashlib.md5(plain).hexdigest()
    del plain
    arc = index.get(md5)
    if not arc:
        return False, "not_in_index", len(blob)
    local = out.joinpath(*arc.split("/"))
    if not local.is_file():
        return False, "archive_file_missing", len(blob)
    if arc not in md5_cache:
        md5_cache[arc] = file_md5(local)
    if md5_cache[arc] != md5:
        return False, "archive_md5_mismatch", len(blob)
    if remote.get(arc) != md5:
        return False, "not_on_remote" if arc not in remote else "remote_md5_mismatch", len(blob)
    return True, "ok", len(blob)


def prune(cur, signal_dir: Path, out: Path, days: int, remote: dict, dry: bool,
          now_ms: int | None = None, log=lambda s: None) -> dict:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    att_dir = signal_dir / "attachments.noindex"
    cands = find_candidates(cur, now_ms - int(days) * DAY_MS)
    index = load_index(out)
    stage = out / "_work" / "pruned" / datetime.now().strftime("%Y-%m-%d")
    s = {"days": int(days), "dry_run": bool(dry), "candidates": len(cands), "verified": 0,
         "pruned_files": 0, "pruned_bytes": 0, "kept": {}}
    md5_cache: dict = {}
    for c in cands:
        ok, why, n = verify(c, att_dir, out, index, remote, md5_cache)
        if not ok:
            s["kept"][why] = s["kept"].get(why, 0) + 1
            continue
        s["verified"] += 1
        if dry:
            s["pruned_files"] += 1
            s["pruned_bytes"] += n
            continue
        dst = stage / c["path"]
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(att_dir / c["path"]), str(dst))
        except OSError:
            s["kept"]["move_failed"] = s["kept"].get("move_failed", 0) + 1
            continue
        s["pruned_files"] += 1
        s["pruned_bytes"] += n
    s["staging"] = str(stage) if not dry and s["pruned_files"] else None
    log(f"prune days={days} dry={int(dry)} candidates={s['candidates']} verified={s['verified']} "
        f"files={s['pruned_files']} bytes={s['pruned_bytes']} kept={json.dumps(s['kept'], sort_keys=True)}")
    return s


def open_db(sql_src: Path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sync_export as SE  # noqa: E402
    from sigexport import data  # noqa: E402
    key = SE.stored_key()
    if not key:
        raise SystemExit("no DB key in the Keychain (run sync.py --setup-key)")
    return data.get_signal_database(sql_src, password=None, key=key)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql-src", required=True, help="folder holding sql/db.sqlite (a fresh copy)")
    ap.add_argument("--signal-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--days", type=int, required=True)
    ap.add_argument("--remote", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.days < 1:
        raise SystemExit("--days must be >= 1")
    cur = open_db(Path(a.sql_src))
    remote = remote_media_md5s(a.remote)
    s = prune(cur, Path(a.signal_dir), Path(a.out), a.days, remote, a.dry_run)
    print("PRUNE_SUMMARY " + json.dumps(s, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
