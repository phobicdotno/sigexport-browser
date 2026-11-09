"""Tests for tools/sync_helper.py against tests/fixtures/sync_stub.py (no real sync)."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import sync_helper  # noqa: E402

STUB = ROOT / "tests" / "fixtures" / "sync_stub.py"
TOKEN = "testtoken_ABC123"


class SyncHelperTest(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp(prefix="sync-helper-test-"))
        (self.out / "viewer").mkdir()
        (self.out / "viewer" / "sync_config.js").write_text(
            "window.SYNC_CONFIG={port:0,token:'%s'};\n" % TOKEN, encoding="utf-8")
        os.environ["SYNC_STUB_SECONDS"] = "5"  # outlives the 1.5 s start probe, even on slow CI
        runner = sync_helper.Runner(str(self.out), sys.executable, str(STUB), upload=True)
        # bind to an ephemeral port, then rebuild the handler with that port for Host/Origin checks
        self.httpd = sync_helper.serve(str(self.out), 0, runner)
        self.port = self.httpd.server_address[1]
        self.httpd.RequestHandlerClass = sync_helper.make_handler(str(self.out), self.port, runner)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        deadline = time.time() + 20
        while time.time() < deadline:  # let the stub finish before deleting its folder
            st = sync_helper.read_status(str(self.out))[0]
            if st.get("state") in ("done", "idle", "failed"):
                break
            time.sleep(0.2)
        shutil.rmtree(self.out, ignore_errors=True)

    def call(self, method, path, token=TOKEN, origin=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method)
        if token:
            req.add_header("X-Sync-Token", token)
        if origin:
            req.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, dict(e.headers), json.loads(body) if body else {}

    def test_rejects_missing_or_wrong_token(self):
        self.assertEqual(self.call("GET", "/status", token=None)[0], 401)
        self.assertEqual(self.call("POST", "/sync", token="nope")[0], 401)

    def test_rejects_foreign_origin(self):
        self.assertEqual(self.call("GET", "/status", origin="https://evil.example")[0], 403)
        self.assertEqual(self.call("OPTIONS", "/sync", token=None, origin="https://evil.example")[0], 403)

    def test_null_origin_cors(self):
        code, h, body = self.call("GET", "/status", origin="null")
        self.assertEqual(code, 200)
        self.assertEqual(h.get("Access-Control-Allow-Origin"), "null")
        self.assertEqual(body.get("state"), "idle")
        code, h, _ = self.call("OPTIONS", "/sync", token=None, origin="null")
        self.assertEqual(code, 204)
        self.assertIn("X-Sync-Token", h.get("Access-Control-Allow-Headers", ""))

    def test_sync_runs_reports_busy_then_done(self):
        code, _, body = self.call("POST", "/sync")
        self.assertEqual((code, body), (202, {"started": True}))
        self.assertEqual(self.call("POST", "/sync")[0], 409)
        deadline, st = time.time() + 30, {}
        while time.time() < deadline:
            st = self.call("GET", "/status")[2]
            if st.get("state") == "done":
                break
            time.sleep(0.3)
        self.assertEqual(st.get("state"), "done")
        self.assertEqual((st["added_messages"], st["new_media"]), (12, 3))
        self.assertIn("upload", (self.out / "_state" / "sync.log").read_text())

    def test_missing_sync_script(self):
        self.httpd.RequestHandlerClass = sync_helper.make_handler(
            str(self.out), self.port, sync_helper.Runner(str(self.out), sys.executable, str(self.out / "nope.py"), False))
        code, _, body = self.call("POST", "/sync")
        self.assertEqual(code, 500)
        self.assertIn("not found", body["error"])

    def _media_fixture(self):
        (self.out / "Media" / "Pictures").mkdir(parents=True)
        (self.out / "Media" / "Pictures" / "a1.jpg").write_bytes(b"JPEGDATA")
        (self.out / "Media" / "Pictures" / "a2.mp4").write_bytes(b"MP4DATA")
        (self.out / "secret.txt").write_text("nope")
        (self.out / "viewer" / "data").mkdir()
        msgs = [{"a": [{"path": "../Media/Pictures/a1.jpg", "type": "image", "name": "Sun æ.jpg", "ok": True},
                       {"path": "../Media/Pictures/a2.mp4", "type": "video", "name": "Sun æ.jpg", "ok": True},
                       {"path": "../secret.txt", "type": "image", "name": "x.jpg", "ok": True},
                       {"path": "../Media/../../etc/hosts", "type": "image", "name": "h.jpg", "ok": True},
                       {"path": "../Media/Pictures/gone.jpg", "type": "image", "name": "g.jpg", "ok": False}]}]
        chat = {"id": 7, "dir": "", "name": "Test chat", "messages": msgs}
        (self.out / "viewer" / "data" / "7.js").write_text(
            "window.CHATS=window.CHATS||{};window.CHATS[7]=" + json.dumps(chat) + ";", encoding="utf-8")

    def test_media_zip_ticket_and_traversal(self):
        import io
        import zipfile
        self._media_fixture()
        self.assertEqual(self.call("POST", "/zip-ticket?chat=7", token=None)[0], 401)
        self.assertEqual(self.call("POST", "/zip-ticket?chat=../7")[0], 404)
        code, _, body = self.call("POST", "/zip-ticket?chat=7", origin="null")
        self.assertEqual((code, body["files"]), (200, 2))
        bad = self.call("GET", "/media.zip?chat=7&ticket=wrong", token=None)
        self.assertEqual(bad[0], 401)
        url = "http://127.0.0.1:%d/media.zip?chat=7&ticket=%s" % (self.port, body["ticket"])
        with urllib.request.urlopen(url, timeout=10) as r:
            self.assertIn("attachment", r.headers["Content-Disposition"])
            data = r.read()
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertEqual(sorted(z.namelist()), ["Sun æ (2).jpg", "Sun æ.jpg"])
        self.assertEqual(z.read("Sun æ.jpg"), b"JPEGDATA")
        with self.assertRaises(urllib.error.HTTPError):  # single use
            urllib.request.urlopen(url, timeout=10)

    def test_log_tail_current_run_and_failed_count(self):
        st = self.out / "_state"
        st.mkdir()
        (st / "sync.log").write_text("\n".join([
            "t step=disk progress=0.02", "t chat NNN=001 failed (RuntimeError); restoring backup",
            "t end state=failed added=1 media=0 chats=0",
            "t step=disk progress=0.02", "t chat NNN=002 new=0 add=3 media=0",
            "t chat NNN=003 failed (RuntimeError); restoring backup",
            "t chat NNN=004 failed (RuntimeError); restoring backup", "t upload rc=0 check rc=0",
            "t end state=failed added=3 media=0 chats=0"]) + "\n")
        (st / "sync_status.json").write_text(json.dumps({"state": "failed", "message": "some chats failed"}))
        code, _, body = self.call("GET", "/log?lines=5000", origin="null")
        self.assertEqual(code, 200)
        self.assertEqual(body["lines"][0], "t step=disk progress=0.02")
        self.assertEqual((len(body["lines"]), body["failed_chats"]), (6, 2))
        self.assertEqual(len(self.call("GET", "/log?lines=2")[2]["lines"]), 2)
        self.assertEqual(self.call("GET", "/log", token=None)[0], 401)
        self.assertEqual(self.call("GET", "/status")[2]["failed_chats"], 2)
        self.assertLessEqual(sync_helper.LOG_MAX, 500)

    def test_print_status_stale(self):
        st = self.out / "_state"
        st.mkdir()
        (st / "sync_status.json").write_text(json.dumps({"state": "done", "added_messages": 2, "new_media": 1}))
        from io import StringIO
        from contextlib import redirect_stdout
        buf = StringIO()
        with redirect_stdout(buf):
            sync_helper.print_status(str(self.out), time.time() + 100)
        self.assertTrue(buf.getvalue().startswith("stale\t2\t1"))


if __name__ == "__main__":
    unittest.main()
