"""Client side of the archive-on-another-machine mode (ARCHIVE_URL): talks to tools/archive_server.py.

The Mac keeps Signal Desktop, the DB snapshot and OUT/_work (sync_tmp, the watermarks cache);
the archive host keeps the archive, the authoritative _state and the Jottacloud upload.
Auth: header "Authorization: Bearer <token>", token read from a 600 file (ARCHIVE_TOKEN_FILE,
default ~/.config/sigexport/archive_token). stdlib only.
"""
from __future__ import annotations

import json
import os
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_TOKEN_FILE = Path.home() / ".config" / "sigexport" / "archive_token"
# top-level files of a sigexport sync_tmp that the archive host needs for the merge + watermarks
META_FILES = ("_fingerprints.tsv", "_selected.tsv", "_unavail.tsv", "_cids.tsv")
CHAT_FILES = ("data.json", "chat.md", "index.html")  # plus everything under <chat>/media/


class ArchiveError(Exception):
    pass


def read_token_file(p) -> str:
    p = Path(p or DEFAULT_TOKEN_FILE).expanduser()
    tok = p.read_text(encoding="utf-8").strip()
    if not tok:
        raise ArchiveError(f"empty token file {p}")
    return tok


def pack(tmp: Path, dest: Path) -> int:
    """tar.gz of the export in tmp: the META_FILES plus each chat's CHAT_FILES and media/.
    Regular files and dirs only (symlinks skipped). Returns the number of files."""
    tmp, n = Path(tmp), 0
    with tarfile.open(dest, "w:gz") as tf:
        for name in META_FILES:
            if (tmp / name).is_file():
                tf.add(tmp / name, arcname=name, recursive=False)
                n += 1
        for chat in sorted(d for d in tmp.iterdir() if d.is_dir() and not d.is_symlink()):
            if not (chat / "data.json").is_file():
                continue
            for fn in CHAT_FILES:
                f = chat / fn
                if f.is_file() and not f.is_symlink():
                    tf.add(f, arcname=f.relative_to(tmp).as_posix(), recursive=False)
                    n += 1
            if not (chat / "media").is_dir() or (chat / "media").is_symlink():
                continue
            for root, dirs, files in os.walk(chat / "media"):
                dirs[:] = sorted(d for d in dirs if not (Path(root) / d).is_symlink())
                for fn in sorted(files):
                    f = Path(root) / fn
                    if f.is_symlink() or not f.is_file():
                        continue
                    tf.add(f, arcname=f.relative_to(tmp).as_posix(), recursive=False)
                    n += 1
    return n


class ArchiveClient:
    def __init__(self, url: str, token: str, timeout: float = 60):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def _req(self, method: str, path: str, body=None, ctype="application/json", timeout=None, raw=False):
        data = None
        if body is not None:
            data = body if isinstance(body, (bytes, bytearray)) or hasattr(body, "read") \
                else json.dumps(body).encode()
        rq = urllib.request.Request(self.url + path, data=data, method=method,
                                    headers={"Authorization": "Bearer " + self.token, "Content-Type": ctype})
        if hasattr(body, "read"):
            rq.add_header("Content-Length", str(os.fstat(body.fileno()).st_size))
        try:
            with urllib.request.urlopen(rq, timeout=timeout or self.timeout) as r:
                b = r.read()
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}").get("error", "")
            except ValueError:
                msg = ""
            raise ArchiveError(f"{method} {path.split('?')[0]} -> HTTP {e.code} {msg}".strip()) from None
        except (urllib.error.URLError, OSError) as e:
            raise ArchiveError(f"{method} {path.split('?')[0]} unreachable ({type(e).__name__})") from None
        return b if raw else json.loads(b or b"{}")

    def state(self) -> dict:
        return self._req("GET", "/state")

    def media_index(self) -> str:
        return self._req("GET", "/media_index", raw=True).decode("utf-8")

    def ingest(self, tar_path: Path, full: bool = False) -> dict:
        with open(tar_path, "rb") as f:
            return self._req("POST", "/ingest" + ("?full=1" if full else ""), f,
                             ctype="application/gzip", timeout=6 * 3600)

    def upload(self, run_id: str, full_check: bool = False) -> dict:
        q = urllib.parse.urlencode({"run": run_id, **({"full_check": "1"} if full_check else {})})
        return self._req("POST", "/upload?" + q, b"", timeout=12 * 3600)

    def media_md5(self, run_id: str, paths, remote: bool = True) -> dict:
        """{"files": {arc: {"archive": md5|None, "remote": md5|None}}}; the host refuses unless run_id
        merged and uploaded clean. remote=False skips the host's rclone md5sum."""
        return self._req("POST", "/media_md5", {"run": run_id, "paths": sorted(paths), "remote": remote},
                         timeout=3600)

    def post_status(self, st: dict) -> None:
        self._req("POST", "/status", st, timeout=5)

    def claim_sync_request(self) -> bool:
        return bool(self._req("POST", "/sync-request?claim=1", b"", timeout=15).get("pending"))
