"""Archive on another machine: Mac-mode sync.py <-> tools/archive_server.py (in-process, 127.0.0.1)."""
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync as S  # noqa: E402
import archive_client as AC  # noqa: E402
import archive_server as AS  # noqa: E402
from test_sync import msg, write_chat  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        r = Path(self.td.name)
        self.host, self.mac, self.exp = r / "host", r / "mac", r / "exp"
        (self.host / "_state").mkdir(parents=True)
        write_chat(self.host / "001_A" / "A", [msg("2026-01-01T10:00:00.000000", "A", "one")])
        (self.host / "_state" / "conv_map.tsv").write_text("cidA\t001_A\tA\n", encoding="utf-8")
        (self.host / "_state" / "folders.tsv").write_text("1\tA\tA\n", encoding="utf-8")
        (self.host / "viewer").mkdir()
        (self.host / "viewer" / "sync_config.js").write_text("window.SYNC_CONFIG={token:'host'}", encoding="utf-8")
        self.tf, self.vtf = r / "tok", r / "vtok"
        self.tf.write_text("mactoken123\n")
        self.vtf.write_text("viewertoken456\n")
        self.arch = AS.Archive(self.host, "dummy:remote")
        self.httpd = AS.serve(self.arch, "127.0.0.1", 0, self.tf, self.vtf)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.td.cleanup()

    def req(self, method, path, token="mactoken123", body=None):
        rq = urllib.request.Request(self.url + path, data=body, method=method,
                                    headers={"Authorization": "Bearer " + token} if token else {})
        try:
            with urllib.request.urlopen(rq, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def mac_sync(self, *extra):
        return S.main(["--out", str(self.mac), "--from-export", str(self.exp), "--min-free", "0",
                       "--archive-url", self.url, "--archive-token-file", str(self.tf), *extra])

    def export(self):
        write_chat(self.exp / "A", [msg("2026-01-01T10:00:00.000000", "A", "one"),
                                    msg("2026-01-02T10:00:00.000000", "A", "two")])
        write_chat(self.exp / "B", [msg("2026-01-03T10:00:00.000000", "B", "pic",
                                        attachments=[{"name": "p.jpg", "path": "media/p.jpg", "missing_kind": None}])])
        (self.exp / "B" / "media").mkdir()
        (self.exp / "B" / "media" / "p.jpg").write_bytes(b"jpegdata")
        (self.exp / "_cids.tsv").write_text("cidA\tA\ncidB\tB\n", encoding="utf-8")
        (self.exp / "_fingerprints.tsv").write_text("cidA\t2:2:2\ncidB\t1:1:1\n", encoding="utf-8")
        (self.exp / "_selected.tsv").write_text("cidA\ncidB\n", encoding="utf-8")

    def test_ingest_round_trip_merges_and_builds_viewer(self):
        self.export()
        self.assertEqual(self.mac_sync(), 0)
        data = (self.host / "001_A" / "A" / "data.json").read_text(encoding="utf-8")
        self.assertIn('"two"', data)
        new = [d for d in self.host.iterdir() if d.name.startswith("002_")]
        self.assertEqual(len(new), 1)
        self.assertIn("cidB\t002_", (self.host / "_state" / "conv_map.tsv").read_text(encoding="utf-8"))
        self.assertTrue((self.host / "viewer" / "index_data.js").is_file(), "viewer built on the host")
        self.assertEqual((self.host / "viewer" / "sync_config.js").read_text(encoding="utf-8"),
                         "window.SYNC_CONFIG={token:'host'}")
        self.assertIn("cidA\t2:2:2", (self.host / "_state" / S.WATERMARKS).read_text(encoding="utf-8"))
        owed = (self.host / "_work" / "upload_pending.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn("001_A/A/data.json", owed)
        self.assertFalse(any(o.endswith("sync_config.js") for o in owed))
        st = json.loads((self.mac / "_state" / "sync_status.json").read_text())
        self.assertEqual((st["state"], st["added_messages"], st["new_chats"]), ("done", 2, 1))
        self.assertFalse((self.mac / "001_A").exists(), "no archive on the Mac")
        mac_st = json.loads((self.host / "_state" / "mac_status.json").read_text())
        self.assertEqual(mac_st["state"], "done")

    def _tar(self, add):
        b = io.BytesIO()
        with tarfile.open(fileobj=b, mode="w") as tf:
            add(tf)
        return b.getvalue()

    def _file(self, tf, name, data=b"x", **kw):
        ti = tarfile.TarInfo(name)
        ti.size = len(data)
        for k, v in kw.items():
            setattr(ti, k, v)
        tf.addfile(ti, io.BytesIO(data) if ti.isfile() else None)

    def test_malicious_tars_rejected(self):
        def link(kind, name, target):
            def f(tf):
                ti = tarfile.TarInfo(name)
                ti.type, ti.linkname = kind, target
                tf.addfile(ti)
            return f
        bad = {
            "absolute": lambda tf: self._file(tf, "/etc/passwd"),
            "dotdot": lambda tf: self._file(tf, "A/../../evil/data.json"),
            "dotdot media": lambda tf: self._file(tf, "A/media/../../x"),
            "symlink": link(tarfile.SYMTYPE, "A/media/l", "/etc/passwd"),
            "hardlink": link(tarfile.LNKTYPE, "A/data.json", "/etc/passwd"),
            "sync_config": lambda tf: self._file(tf, "viewer/sync_config.js"),
            "sync_config in chat": lambda tf: self._file(tf, "A/media/sync_config.js"),
            "state": lambda tf: self._file(tf, "_state/media_index.tsv"),
            "other top": lambda tf: self._file(tf, "evil.sh"),
            "other chat file": lambda tf: self._file(tf, "A/run.py"),
            "fifo": lambda tf: self._file(tf, "A/media/f", type=tarfile.FIFOTYPE),
            "backslash": lambda tf: self._file(tf, "A\\..\\x"),
        }
        before = (self.host / "001_A" / "A" / "data.json").read_text(encoding="utf-8")
        for why, add in bad.items():
            with self.subTest(why):
                code, j = self.req("POST", "/ingest", body=self._tar(add))
                self.assertEqual(code, 200)
                self.assertEqual(j["rc"], 2, j)
                self.assertIn("rejected", j["message"])
        self.assertEqual((self.host / "001_A" / "A" / "data.json").read_text(encoding="utf-8"), before)
        self.assertFalse(Path("/tmp/evil").exists())
        self.assertFalse(list((self.host / "_work" / "ingest").iterdir()))
        # size limit
        tmp = Path(self.td.name) / "big.tar"
        tmp.write_bytes(self._tar(lambda tf: self._file(tf, "A/data.json", b"y" * 100)))
        with self.assertRaises(AS.Reject):
            AS.safe_extract(tmp, Path(self.td.name) / "x", max_bytes=10)
        # no token / viewer token
        self.assertEqual(self.req("POST", "/ingest", token=None, body=b"")[0], 401)
        self.assertEqual(self.req("POST", "/ingest", token="viewertoken456", body=b"")[0], 403)
        self.assertEqual(self.req("GET", "/state", token="wrong")[0], 401)

    def test_precheck_uses_remote_watermarks(self):
        (self.host / "_state" / S.WATERMARKS).write_text("#last_full\t%d\ncidA\t1:1:1\t0\n" % int(S.time.time()),
                                                          encoding="utf-8")
        seen, orig = {}, (S.snapshot, S.run_precheck, S.run_export)
        try:
            S.snapshot = lambda run, q: Path(self.td.name) / "src"
            S.run_precheck = lambda run, src, pre, wm=None: seen.update(wm=wm, text=Path(wm).read_text()) or set()
            S.run_export = lambda *a, **k: seen.update(export=True)
            rc = S.main(["--out", str(self.mac), "--min-free", "0", "--archive-url", self.url,
                         "--archive-token-file", str(self.tf)])
        finally:
            S.snapshot, S.run_precheck, S.run_export = orig
        self.assertEqual(rc, 0)
        self.assertEqual(Path(seen["wm"]), self.mac.resolve() / "_work" / S.WATERMARKS)
        self.assertIn("cidA\t1:1:1", seen["text"])
        self.assertNotIn("export", seen)
        st = json.loads((self.mac / "_state" / "sync_status.json").read_text())
        self.assertEqual(st["message"], "no changes")
        self.assertFalse((self.mac / "_state" / S.WATERMARKS).exists())

    def _with_upload(self, ok):
        calls, orig_up, orig_pr = [], S.upload_owed, S.run_prune_remote
        S.upload_owed = lambda run, remote: (0, 0 if ok else 1)
        S.run_prune_remote = lambda *a, **k: calls.append("prune") or {"pruned_files": 1}
        try:
            self.export()
            rc = self.mac_sync("--upload", "--prune-signal-days", "30")
        finally:
            S.upload_owed, S.run_prune_remote = orig_up, orig_pr
        return rc, calls

    def test_prune_refused_unless_remote_run_clean(self):
        rc, calls = self._with_upload(ok=False)
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [])
        self.assertIn("prune skipped", (self.mac / "_state" / "sync.log").read_text(encoding="utf-8"))
        rid = max(self.arch.runs())
        code, j = self.req("POST", "/media_md5", body=json.dumps({"run": rid, "paths": ["Media/Pictures/x.jpg"]}).encode())
        self.assertEqual(code, 409)
        self.assertFalse(S.remote_prune_ok({"rc": 0, "run_id": "r"}, {"ok": False, "run_id": "r"}))
        self.assertFalse(S.remote_prune_ok({"rc": 1, "run_id": "r"}, {"ok": True, "run_id": "r"}))
        self.assertFalse(S.remote_prune_ok({"rc": 0, "run_id": "r"}, {"ok": True, "run_id": "other"}))

    def test_prune_runs_after_clean_remote_run(self):
        rc, calls = self._with_upload(ok=True)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["prune"])
        rid = max(self.arch.runs())
        (self.host / "Media" / "Pictures").mkdir(parents=True, exist_ok=True)
        (self.host / "Media" / "Pictures" / "x.jpg").write_bytes(b"x")
        j = self.arch.media_md5(rid, ["Media/Pictures/x.jpg", "Media/Pictures/none.jpg"], True,
                                remote_md5=lambda arcs: {"Media/Pictures/x.jpg": "abc"})
        self.assertEqual(j["files"]["Media/Pictures/x.jpg"], {"archive": S.file_md5(self.host / "Media/Pictures/x.jpg"),
                                                              "remote": "abc"})
        self.assertIsNone(j["files"]["Media/Pictures/none.jpg"]["archive"])
        with self.assertRaises(AS.Reject):
            self.arch.media_md5(rid, ["../_state/x"], False)

    def test_sync_request_flow(self):
        code, j = self.req("POST", "/sync-request", token="viewertoken456", body=b"")
        self.assertEqual((code, j.get("started")), (202, True))
        code, st = self.req("GET", "/status", token="viewertoken456")
        self.assertEqual((st["state"], st["step"]), ("running", "queued"))
        self.assertEqual(self.req("POST", "/sync-request?claim=1", token="viewertoken456", body=b"")[0], 403)
        self.assertEqual(self.req("POST", "/status", token="viewertoken456", body=b"{}")[0], 403)
        self.assertEqual(self.req("GET", "/state", token="viewertoken456")[0], 403)
        c = AC.ArchiveClient(self.url, "mactoken123")
        self.assertTrue(c.claim_sync_request())
        self.assertFalse(c.claim_sync_request())
        c.post_status({"state": "running", "step": "export", "progress": 0.1, "nested": {"no": 1}})
        code, st = self.req("GET", "/status", token="viewertoken456")
        self.assertEqual((st["state"], st["step"]), ("running", "export"))
        self.assertNotIn("nested", st)
        # the Mac helper's poller starts a sync when one is pending
        sys.path.insert(0, str(REPO / "tools"))
        import sync_helper as SH
        started = []

        class R:
            def start(self):
                started.append(1)
                return 202, {"started": True}
        self.req("POST", "/sync-request", token="viewertoken456", body=b"")
        self.assertTrue(SH.poll_archive_once(c, R()))
        self.assertFalse(SH.poll_archive_once(c, R()))
        self.assertEqual(started, [1])


if __name__ == "__main__":
    unittest.main()
