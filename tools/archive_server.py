#!/usr/bin/env python3
"""Archive server: runs on the machine that holds the archive (e.g. a Linux home server), so the
archive and the viewer can live off the Mac. The Mac still exports (Signal Desktop is there) and
streams the changed chats here; this server runs the existing merge (pipeline/sync.py: append-only,
central media, link check, rollback), rebuilds the viewer, keeps the authoritative _state
(watermarks, media_index, conv_map, folders) and owns the Jottacloud upload.

  python3 tools/archive_server.py --root /srv/signal-archive --token-file /etc/sigexport/archive_token \\
      [--viewer-token-file F] [--bind 127.0.0.1] [--port 8766] [--remote jotta:SignalArchive]

Endpoints (JSON; every one needs "Authorization: Bearer <token>" or "X-Sync-Token: <token>"):
  GET  /state          watermarks, conv_map, media_index digest, VERSION, upload_owed, last_run
  GET  /media_index    _state/media_index.tsv (text)
  POST /ingest         body: tar(.gz) of the Mac's _work/sync_tmp (chat dirs with data.json, chat.md,
                       media/ + _fingerprints/_selected/_unavail/_cids.tsv) -> merge, viewer, watermarks
                       -> {"run_id", "rc", "message", "status"}; 409 while a merge/upload runs
  POST /upload?run=ID[&full_check=1]  rclone copy + check of the files ID (and earlier runs) changed
                       -> {"run_id", "ok", "rc", "check_rc", "files"}
  POST /media_md5      {"run": ID, "paths": [...], "remote": bool} -> {"files": {arc: {"archive", "remote"}}}
                       refused (409) unless run ID merged clean AND its upload check passed
  GET  /status         the Mac's last reported status (POST /status), a queued sync request shows as
                       state=running step=queued; "archive" holds this host's own sync_status.json
  POST /status         (Mac) store its sync_status.json
  GET  /log?lines=N    tail of this host's _state/sync.log
  POST /sync-request   (viewer) queue a sync -> 202 {"started": true, "queued": true}
  POST /sync-request?claim=1   (Mac helper poll) -> {"pending": bool}; clears the request
The optional viewer token (--viewer-token-file, the token in the hosted viewer's sync_config.js) is
accepted only for GET /status, GET /log and POST /sync-request (no claim).

Security: ingest only accepts regular files and dirs under <chat>/{data.json,chat.md,index.html,media/...} and
the four top-level .tsv files (no absolute paths, no "..", no links, no devices, nothing named
sync_config*, size and count limits) and extracts them itself (never tarfile.extract).
Logs counts and NNN only.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "pipeline"))
import sync as S  # noqa: E402

DEFAULT_PORT = 8766
META_FILES = {"_fingerprints.tsv", "_selected.tsv", "_unavail.tsv", "_cids.tsv"}
CHAT_FILES = {"data.json", "chat.md", "index.html"}
MAX_INGEST = 8 * 1024 ** 3      # bytes, compressed body and extracted total
MAX_MEMBERS = 200_000
MAX_JSON = 1024 ** 2
VIEWER_ONLY = {("GET", "/status"), ("GET", "/log"), ("POST", "/sync-request")}
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")


class Reject(Exception):
    pass


def read_token(p) -> str:
    try:
        return Path(p).read_text(encoding="utf-8").strip() if p else ""
    except OSError:
        return ""


def check_member(m: tarfile.TarInfo) -> PurePosixPath | None:
    """Validated relative path of a tar member, None for a dir; raises Reject for anything else."""
    name = m.name
    if not name or "\\" in name or "\x00" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise Reject("absolute or odd path")
    p = PurePosixPath(name)
    parts = [x for x in p.parts if x != "."]
    if not parts or any(x == ".." for x in parts):
        raise Reject("path escapes")
    if any(x.lower().startswith("sync_config") or x.lower() in ("viewer", "_state", "_work") for x in parts):
        raise Reject("forbidden name")
    if m.issym() or m.islnk():
        raise Reject("link")
    if m.isdir():
        if len(parts) > 1 and parts[1] != "media":
            raise Reject("unexpected dir")
        return None
    if not m.isfile():
        raise Reject("not a regular file")
    p = PurePosixPath(*parts)
    if len(parts) == 1:
        if parts[0] not in META_FILES:
            raise Reject("unexpected top-level file")
    elif len(parts) == 2:
        if parts[1] not in CHAT_FILES:
            raise Reject("unexpected chat file")
    elif parts[1] != "media":
        raise Reject("unexpected chat path")
    return p


def safe_extract(tar_path: Path, dest: Path, max_bytes: int = MAX_INGEST, max_members: int = MAX_MEMBERS) -> int:
    """Validate EVERY member first, then write the regular files ourselves. Returns files written."""
    dest.mkdir(parents=True, exist_ok=False)
    with tarfile.open(tar_path, "r:*") as tf:
        members, total, seen = [], 0, set()
        for m in tf:
            if len(members) >= max_members:
                raise Reject("too many members")
            rel = check_member(m)
            if rel is None:
                continue
            total += m.size
            if total > max_bytes:
                raise Reject("too large")
            if str(rel) in seen:
                raise Reject("duplicate member")
            seen.add(str(rel))
            members.append((m, rel))
        root = dest.resolve()
        for m, rel in members:
            out = dest.joinpath(*rel.parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            if root not in out.parent.resolve().parents and out.parent.resolve() != root:
                raise Reject("path escapes")
            src = tf.extractfile(m)
            with open(out, "xb") as f:
                n = 0
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    n += len(b)
                    if n > m.size:
                        raise Reject("member larger than declared")
                    f.write(b)
    return len(members)


def safe_arc(arc: str) -> bool:
    p = PurePosixPath(arc)
    return (arc.startswith("Media/") and "\\" not in arc and not arc.startswith("/")
            and ".." not in p.parts and len(p.parts) >= 3)


class Archive:
    """State + operations of the archive host. One merge/upload at a time (OUT/_state/sync.lock)."""

    def __init__(self, root: Path, remote: str | None, max_bytes: int = MAX_INGEST):
        self.root, self.remote, self.max_bytes = Path(root), remote, max_bytes
        self.state = self.root / "_state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.mu = threading.Lock()

    # ---- run registry: {run_id: {"rc": int, "upload_ok": bool|None, "t": epoch}}
    def runs(self) -> dict:
        p = self.state / "archive_runs.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_run(self, run_id: str, **kw) -> None:
        r = self.runs()
        r.setdefault(run_id, {"t": int(time.time())}).update(kw)
        keep = dict(sorted(r.items(), key=lambda kv: kv[1].get("t", 0))[-50:])
        S.D.atomic_write(str(self.state / "archive_runs.json"), json.dumps(keep, indent=1))

    def state_doc(self) -> dict:
        rd = lambda n: (self.state / n).read_text(encoding="utf-8") if (self.state / n).is_file() else ""  # noqa: E731
        mi = (self.state / "media_index.tsv")
        h = hashlib.sha256(mi.read_bytes()).hexdigest() if mi.is_file() else ""
        ver = ""
        for v in (self.root / "viewer" / "VERSION", HERE.parent / "VERSION"):
            if v.is_file():
                ver = v.read_text(encoding="utf-8").strip()
                break
        runs = self.runs()
        last = max(runs, key=lambda k: runs[k].get("t", 0)) if runs else None
        return {"version": ver, "watermarks": rd(S.WATERMARKS), "conv_map": rd("conv_map.tsv"),
                "media_index_sha256": h, "media_index_lines": rd("media_index.tsv").count("\n"),
                "upload_owed": len(S.owed_files(self.root)), "last_run": last}

    def _locked(self, fn):
        lock = self.state / "sync.lock"
        try:
            S.acquire_lock(lock)
        except S.Busy:
            raise Reject("busy")
        try:
            return fn()
        finally:
            try:
                if lock.read_text().strip() == str(os.getpid()):
                    lock.unlink()
            except OSError:
                pass

    def ingest(self, tar_path: Path) -> dict:
        run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
        tmp = self.root / "_work" / "ingest" / run_id

        def go():
            run = S.Run(self.root, False)
            try:
                run.step("ingest", 0.05)
                n = safe_extract(tar_path, tmp, self.max_bytes)
                run.log(f"ingest run={run_id} files={n}")
                try:
                    v = S.deploy_viewer(self.root)
                    if v:
                        run.log(f"viewer code updated to v{v.split(' ')[0]}")
                except Exception as ex:  # noqa: BLE001
                    run.log(f"viewer code update failed ({type(ex).__name__})")
                run.step("merge", 0.3)
                rc = S.sync(run, tmp, False, None, record_upload=True)
                msg = "ok" if rc == 0 else "some chats failed, see sync.log"
                run.end(rc == 0, msg)
            except Reject as ex:
                run.end(False, f"ingest rejected: {ex}")
                rc, msg = 2, f"ingest rejected: {ex}"
            except Exception as ex:  # noqa: BLE001
                run.end(False, type(ex).__name__)
                rc, msg = 1, type(ex).__name__
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            self._save_run(run_id, rc=rc, upload_ok=None)
            return {"run_id": run_id, "rc": rc, "message": msg, "status": run.st}
        return self._locked(go)

    def upload(self, run_id: str, full_check: bool) -> dict:
        if run_id not in self.runs():
            raise Reject("unknown run")
        if not self.remote:
            raise Reject("no REMOTE configured on the archive host")

        def go():
            run = S.Run(self.root, False)
            run.full_check = full_check
            try:
                run.step("upload", 0.9)
                files = len(S.owed_files(self.root))
                rc, crc = S.upload_owed(run, self.remote)
                run.end(rc == 0 and crc == 0, "upload ok" if not rc and not crc else "upload failed")
            except SystemExit as ex:
                run.end(False, str(ex.code))
                rc, crc, files = 1, 1, 0
            ok = rc == 0 and crc == 0
            self._save_run(run_id, upload_ok=ok)
            return {"run_id": run_id, "ok": ok, "rc": rc, "check_rc": crc, "files": files}
        return self._locked(go)

    def media_md5(self, run_id: str, paths: list, remote: bool, remote_md5=None) -> dict:
        r = self.runs().get(run_id)
        if not r or r.get("rc") != 0 or r.get("upload_ok") is not True:
            raise Reject("run did not merge and upload clean")
        if not isinstance(paths, list) or len(paths) > 100_000 or not all(isinstance(p, str) and safe_arc(p) for p in paths):
            raise Reject("bad paths")
        files = {}
        for arc in paths:
            f = self.root.joinpath(*arc.split("/"))
            files[arc] = {"archive": S.file_md5(f) if f.is_file() and not f.is_symlink() else None, "remote": None}
        if remote and paths:
            if remote_md5 is None:
                import prune_signal as PR
                remote_md5 = lambda arcs: PR.remote_media_md5s(self.remote, arcs)  # noqa: E731
            got = remote_md5(set(paths))
            import unicodedata
            got = {unicodedata.normalize("NFC", k): v for k, v in got.items()}
            for arc in paths:
                files[arc]["remote"] = got.get(unicodedata.normalize("NFC", arc))
        return {"files": files}

    # ---- hosted Sync button
    def request_sync(self) -> dict:
        p = self.state / "sync_request.json"
        if not p.is_file():
            S.D.atomic_write(str(p), json.dumps({"requested": S.now_iso()}))
        return {"started": True, "queued": True}

    def claim_request(self) -> dict:
        p = self.state / "sync_request.json"
        with self.mu:
            if p.is_file():
                p.unlink()
                return {"pending": True}
        return {"pending": False}

    def status(self) -> dict:
        rd = lambda n: json.loads((self.state / n).read_text(encoding="utf-8")) if (self.state / n).is_file() else None  # noqa: E731
        try:
            mac, own, req = rd("mac_status.json"), rd("sync_status.json"), rd("sync_request.json")
        except ValueError:
            mac = own = req = None
        st = dict(mac or own or {"state": "idle"})
        if req and st.get("state") != "running":
            st = {"state": "running", "step": "queued", "progress": 0.0, "started": req.get("requested"),
                  "finished": None, "added_messages": 0, "new_media": 0, "new_chats": 0,
                  "message": "waiting for the Mac to start the sync"}
        st["archive"] = own
        return st

    def put_mac_status(self, st) -> None:
        if not isinstance(st, dict):
            raise Reject("bad status")
        st = {k: v for k, v in st.items() if isinstance(k, str) and len(k) < 64
              and isinstance(v, (str, int, float, bool, type(None)))}
        S.D.atomic_write(str(self.state / "mac_status.json"), json.dumps(st, indent=1))


def make_handler(arch: Archive, token_file, viewer_token_file=None):
    class H(BaseHTTPRequestHandler):
        server_version = "sigexport-archive/1"

        def log_message(self, fmt, *args):  # no paths/queries in logs
            pass

        def _send(self, code, obj, ctype="application/json"):
            body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _who(self):
            got = self.headers.get("Authorization") or ""
            got = got[7:].strip() if got.startswith("Bearer ") else (self.headers.get("X-Sync-Token") or "")
            main, view = read_token(token_file), read_token(viewer_token_file)
            if main and got and hmac.compare_digest(main.encode(), got.encode()):
                return "mac"
            if view and got and hmac.compare_digest(view.encode(), got.encode()):
                return "viewer"
            return None

        def _guard(self, method, path, q):
            who = self._who()
            if who is None:
                self._send(401, {"error": "bad or missing token"})
                return None
            if who == "viewer" and ((method, path) not in VIEWER_ONLY or q.get("claim")):
                self._send(403, {"error": "not allowed with the viewer token"})
                return None
            return who

        def _json_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_JSON:
                raise Reject("too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            if not self._guard("GET", u.path, q):
                return
            if u.path == "/state":
                self._send(200, arch.state_doc())
            elif u.path == "/media_index":
                p = arch.state / "media_index.tsv"
                self._send(200, p.read_bytes() if p.is_file() else b"", "text/plain; charset=utf-8")
            elif u.path == "/status":
                self._send(200, arch.status())
            elif u.path == "/log":
                try:
                    n = max(1, min(500, int((q.get("lines") or ["200"])[0])))
                except ValueError:
                    n = 200
                p = arch.state / "sync.log"
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:] if p.is_file() else []
                self._send(200, {"lines": lines, "failed_chats": sum(" failed (" in x for x in lines),
                                 "state": arch.status().get("state", "idle")})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            if not self._guard("POST", u.path, q):
                return
            try:
                if u.path == "/ingest":
                    self._ingest()
                elif u.path == "/upload":
                    rid = (q.get("run") or [""])[0]
                    if not RUN_ID.match(rid):
                        raise Reject("bad run id")
                    self._send(200, arch.upload(rid, bool(q.get("full_check"))))
                elif u.path == "/media_md5":
                    b = self._json_body()
                    rid = str(b.get("run") or "")
                    if not RUN_ID.match(rid):
                        raise Reject("bad run id")
                    self._send(200, arch.media_md5(rid, b.get("paths"), bool(b.get("remote", True))))
                elif u.path == "/status":
                    arch.put_mac_status(self._json_body())
                    self._send(200, {"ok": True})
                elif u.path == "/sync-request":
                    if q.get("claim"):
                        self._send(200, arch.claim_request())
                    else:
                        self._send(202, arch.request_sync())
                else:
                    self._send(404, {"error": "not found"})
            except Reject as ex:
                self._send(409 if str(ex) in ("busy", "run did not merge and upload clean") else 400,
                           {"error": str(ex), "busy": str(ex) == "busy"})
            except ValueError:
                self._send(400, {"error": "bad request"})

        def _ingest(self):
            n = int(self.headers.get("Content-Length") or -1)
            if n < 0:
                raise Reject("Content-Length required")
            if n > arch.max_bytes:
                raise Reject("too large")
            work = arch.root / "_work"
            work.mkdir(parents=True, exist_ok=True)
            tp = work / f"ingest_{secrets.token_hex(4)}.tar"
            try:
                with open(tp, "wb") as f:
                    left = n
                    while left:
                        b = self.rfile.read(min(1 << 20, left))
                        if not b:
                            raise Reject("short body")
                        f.write(b)
                        left -= len(b)
                self._send(200, arch.ingest(tp))
            finally:
                tp.unlink(missing_ok=True)
    return H


def serve(arch: Archive, bind: str, port: int, token_file, viewer_token_file=None):
    httpd = ThreadingHTTPServer((bind, port), make_handler(arch, token_file, viewer_token_file))
    httpd.daemon_threads = True
    return httpd


def main(argv=None) -> int:
    e = os.environ.get
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=e("ARCHIVE_ROOT", "/srv/signal-archive"))
    ap.add_argument("--token-file", default=e("ARCHIVE_TOKEN_FILE", "/etc/sigexport/archive_token"))
    ap.add_argument("--viewer-token-file", default=e("ARCHIVE_VIEWER_TOKEN_FILE", ""))
    ap.add_argument("--bind", default=e("ARCHIVE_BIND", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(e("ARCHIVE_PORT", DEFAULT_PORT)))
    ap.add_argument("--remote", default=e("REMOTE", ""))
    ap.add_argument("--max-ingest-gib", type=float, default=float(e("MAX_INGEST_GIB", "8")))
    a = ap.parse_args(argv)
    if not read_token(a.token_file):
        raise SystemExit(f"no token in {a.token_file}")
    arch = Archive(Path(a.root), a.remote or None, int(a.max_ingest_gib * 1024 ** 3))
    httpd = serve(arch, a.bind, a.port, a.token_file, a.viewer_token_file or None)
    sys.stderr.write(f"archive server on http://{a.bind}:{a.port}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
