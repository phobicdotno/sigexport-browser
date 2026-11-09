import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sync as S  # noqa: E402


def msg(date, sender, body, **kw):
    return {"date": date, "sender": sender, "body": body, "quote": "", "sticker": None,
            "reactions": [], "attachments": [], "deleted": False, "call": False, "missed": False, **kw}


def write_chat(d: Path, msgs):
    d.mkdir(parents=True, exist_ok=True)
    (d / "data.json").write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs), encoding="utf-8")
    md = "".join(f"[{m['date'][:19].replace('T', ' ')}] {m['sender']}: {m['body']}  \n\n" for m in msgs)
    (d / "chat.md").write_text(md, encoding="utf-8")


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.out = self.root / "OUT"
        self.exp = self.root / "exp"
        (self.out / "_state").mkdir(parents=True)
        self.old = [msg("2026-01-01T10:00:00.000000", "A", "one"),
                    msg("2026-01-01T10:05:00.000000", "B", "phone only", source="phone-backup"),
                    msg("2026-01-01T10:10:00.000000", "A", "old pic",
                        attachments=[{"name": "x.jpg", "path": "../../Media/Pictures/x.jpg", "missing_kind": None}])]
        write_chat(self.out / "001_A" / "A", self.old)
        (self.out / "Media" / "Pictures").mkdir(parents=True)
        (self.out / "Media" / "Pictures" / "x.jpg").write_bytes(b"x")
        (self.out / "_state" / "folders.tsv").write_text("1\tA\tA\n", encoding="utf-8")
        (self.out / "_state" / "id_map.tsv").write_text("", encoding="utf-8")
        self.before = (self.out / "001_A" / "A" / "data.json").read_text(encoding="utf-8")

    def tearDown(self):
        self.td.cleanup()

    def run_sync(self, *extra):
        return S.main(["--out", str(self.out), "--from-export", str(self.exp), "--min-free", "0", *extra])

    def test_append_only_keeps_existing_and_phone(self):
        new = [msg("2026-01-01T10:00:00.000000", "A", "one"),
               msg("2026-01-01T10:10:30.000000", "A", "old pic",  # +30 s identical body -> same
                   attachments=[{"name": "x.jpg", "path": "media/x.jpg", "missing_kind": "file"}]),
               msg("2026-01-01T10:07:00.000000", "A", "in between"),
               msg("2026-01-02T09:00:00.000000", "A", "later")]
        write_chat(self.exp / "A", new)
        self.assertEqual(self.run_sync(), 0)
        after = (self.out / "001_A" / "A" / "data.json").read_text(encoding="utf-8").splitlines()
        old_lines = self.before.splitlines()
        self.assertEqual([l for l in after if l in old_lines], old_lines)
        self.assertEqual(len(after), 5)
        self.assertIn("in between", after[2])
        self.assertIn("later", after[4])
        self.assertTrue(any("phone-backup" in l for l in after))
        md = (self.out / "001_A" / "A" / "chat.md").read_text(encoding="utf-8")
        self.assertLess(md.index("phone only"), md.index("in between"))
        self.assertLess(md.index("in between"), md.index("old pic"))
        st = json.loads((self.out / "_state" / "sync_status.json").read_text())
        self.assertEqual((st["state"], st["added_messages"], st["new_chats"]), ("done", 2, 0))
        self.assertEqual(st["progress"], 1.0)

    def test_missing_old_attachments_ignored(self):
        write_chat(self.exp / "A", [msg("2026-01-01T10:10:00.000000", "A", "old pic",
                                        attachments=[{"name": "x.jpg", "path": "media/x.jpg", "missing_kind": "file"}])])
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual((self.out / "001_A" / "A" / "data.json").read_text(encoding="utf-8"), self.before)

    def test_preexisting_broken_refs_do_not_block_merge(self):
        old = self.old + [msg("2026-01-01T10:20:00.000000", "A", "lost pic",
                              attachments=[{"name": "gone.jpg", "path": "../../Media/Pictures/gone.jpg"}])]
        write_chat(self.out / "001_A" / "A", old)
        write_chat(self.exp / "A", old + [msg("2026-01-03T09:00:00.000000", "A", "newer")])
        self.assertEqual(len(S.linkcheck(self.out / "001_A")), 1)
        self.assertEqual(self.run_sync(), 0)
        after = (self.out / "001_A" / "A" / "data.json").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(after), 5)
        self.assertIn("newer", after[-1])
        self.assertEqual(len(S.linkcheck(self.out / "001_A")), 1)

    def test_new_message_with_attachment_not_in_source_is_deferred_not_broken(self):
        # sigexport lists media/<file> but Signal Desktop has not downloaded it: the merge used to
        # write the ref, the link check failed and the whole chat was rolled back (RuntimeError).
        import time as _t
        recent = _t.strftime("%Y-%m-%dT%H:%M:%S.000000", _t.localtime(_t.time() - 3600))
        new = self.old + [msg("2026-01-03T09:00:00.000000", "A", "newer"),
                          msg(recent, "A", "fresh pic",
                              attachments=[{"name": "y.jpg", "path": "media/y.jpg", "missing_kind": None}])]
        write_chat(self.exp / "A", new)
        self.assertEqual(self.run_sync(), 0)
        after = (self.out / "001_A" / "A" / "data.json").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(after), 4)
        self.assertIn("newer", after[-1])
        st = json.loads((self.out / "_state" / "sync_status.json").read_text())
        self.assertEqual((st["state"], st["added_messages"], st["deferred_messages"]), ("done", 1, 1))
        # once the file is there, the next sync adds it with its media
        (self.exp / "A" / "media").mkdir()
        (self.exp / "A" / "media" / "y.jpg").write_bytes(b"y")
        self.assertEqual(self.run_sync(), 0)
        after = (self.out / "001_A" / "A" / "data.json").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(after), 5)
        self.assertEqual(S.linkcheck(self.out / "001_A"), set())

    def test_old_message_with_absent_attachment_is_flagged(self):
        write_chat(self.exp / "A", self.old + [msg("2020-05-01T09:00:00.000000", "A", "ancient pic",
                   attachments=[{"name": "z.jpg", "path": "media/z.jpg", "missing_kind": None}])])
        self.assertEqual(self.run_sync(), 0)
        after = (self.out / "001_A" / "A" / "data.json").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(after), 4)
        self.assertEqual(json.loads(after[0])["attachments"][0]["missing_kind"], "absent_at_sync")
        self.assertEqual(S.linkcheck(self.out / "001_A"), set())

    def test_failure_detail_logs_check_not_paths(self):
        self.assertEqual(S.failure_detail(S.MergeCheck("link check: 1 new broken refs")),
                         "MergeCheck: link check: 1 new broken refs")
        d = S.failure_detail(FileNotFoundError(2, "No such file", "/x/007_Secret Name/a"))
        self.assertNotIn("Secret", d)
        self.assertIn("errno=2", d)

    def test_new_chat_gets_next_nnn_and_dry_run(self):
        write_chat(self.exp / "Zed", [msg("2026-02-01T10:00:00.000000", "Zed", "hi")])
        (self.exp / "_cids.tsv").write_text("cid-z\tZed\n")
        self.assertEqual(self.run_sync("--dry-run"), 0)
        self.assertFalse((self.out / "002_Zed").exists())
        self.assertEqual(self.run_sync(), 0)
        self.assertTrue((self.out / "002_Zed" / "Zed" / "data.json").is_file())
        self.assertIn("cid-z\t002_Zed\tZed", (self.out / "_state" / "conv_map.tsv").read_text())
        st = json.loads((self.out / "_state" / "sync_status.json").read_text())
        self.assertEqual(st["new_chats"], 1)
        self.assertEqual(self.run_sync(), 0)  # idempotent
        self.assertEqual(len((self.out / "002_Zed" / "Zed" / "data.json").read_text().splitlines()), 1)

    def test_lock_busy_exit_75(self):
        (self.out / "_state" / "sync.lock").write_text(str(os.getppid()))
        write_chat(self.exp / "A", self.old)
        self.assertEqual(self.run_sync(), 75)
        (self.out / "_state" / "sync.lock").write_text("999999")  # stale
        self.assertEqual(self.run_sync(), 0)
        self.assertFalse((self.out / "_state" / "sync.lock").exists())

    def test_conv_map_multi_subfolder_phone_and_ambiguous(self):
        # 076-style folder with two subfolders, a phone-only folder, a renamed chat (cid match)
        write_chat(self.out / "076_Two" / "P", [msg("2026-01-01T10:00:00.000000", "P", "p")])
        write_chat(self.out / "076_Two" / "Q", [msg("2026-01-01T10:00:00.000000", "Q", "q")])
        write_chat(self.out / "210_phone_32" / "phone_32", [msg("2026-01-01T10:00:00.000000", "X", "x")])
        write_chat(self.out / "300_Dup" / "D", [msg("2026-01-01T10:00:00.000000", "D", "d")])
        write_chat(self.out / "301_Dup" / "D", [msg("2026-01-01T10:00:00.000000", "D", "d")])
        (self.out / "_state" / "conv_map.tsv").write_text(
            "cid-p\t076_Two\tP\ncid-q\t076_Two\tQ\nphone:210_phone_32\t210_phone_32\tphone_32\n"
            "cid-a\t001_A\tA\n")
        later = "2026-03-01T10:00:00.000000"
        write_chat(self.exp / "Q", [msg(later, "Q", "new q")])
        write_chat(self.exp / "Renamed", [msg(later, "A", "new a")])     # cid-a under a new name
        write_chat(self.exp / "phone_32", [msg(later, "X", "never")])    # no cid, phone folder
        write_chat(self.exp / "D", [msg(later, "D", "ambiguous")])       # no cid, two folders
        (self.exp / "_cids.tsv").write_text("cid-q\tQ\ncid-a\tRenamed\n")
        self.assertEqual(self.run_sync(), 0)
        self.assertIn("new q", (self.out / "076_Two" / "Q" / "data.json").read_text())
        self.assertNotIn("new q", (self.out / "076_Two" / "P" / "data.json").read_text())
        self.assertIn("new a", (self.out / "001_A" / "A" / "data.json").read_text())
        self.assertNotIn("never", (self.out / "210_phone_32" / "phone_32" / "data.json").read_text())
        for t in ("300_Dup", "301_Dup"):
            self.assertNotIn("ambiguous", (self.out / t / "D" / "data.json").read_text())
        self.assertEqual(sorted(p.name for p in self.out.iterdir() if p.name[:1].isdigit()),
                         ["001_A", "076_Two", "210_phone_32", "300_Dup", "301_Dup"])
        st = json.loads((self.out / "_state" / "sync_status.json").read_text())
        self.assertEqual((st["added_messages"], st["new_chats"]), (2, 0))

    def test_matcher_rules(self):
        old = [msg("2026-01-01T10:00:00.123000", "A", "x", server_ts=5)]
        self.assertEqual(S.new_messages(old, [msg("2026-01-01T10:00:00.123", "A", "edited")]), [])
        self.assertEqual(S.new_messages(old, [msg("2026-01-01T10:00:59", "A", "x")]), [])
        self.assertEqual(len(S.new_messages(old, [msg("2026-01-01T10:01:30", "A", "x")])), 1)
        self.assertEqual(S.new_messages(old, [msg("2026-01-01T11:00:00", "A", "y", server_ts=5)]), [])


    def test_upload_excludes_sync_config_token(self):
        pats = S.load_upload_excludes()
        for want in ("viewer/sync_config.js", "_work/**", "*.pid", "_state/sync.lock", "_state/sync.log",
                     "_state/sync_status.json", "_state/upload.log", "**/.build_tmp/**"):
            self.assertIn(want, pats)
        bad = self.root / "ex.txt"
        bad.write_text("_work/**\n# viewer/sync_config.js\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            S.load_upload_excludes(bad)
        # the real --upload path keeps excluded files out of the --files-from list (rclone refuses
        # --exclude together with --files-from, so no filter flags may be passed alongside it)
        write_chat(self.exp / "A", [msg("2026-01-01T10:00:00.000000", "A", "one"),
                                    msg("2026-01-02T10:00:00.000000", "A", "new")])
        calls, real = [], S.subprocess.run

        def fake(cmd, *a, **kw):
            if cmd and cmd[0] == "rclone":
                calls.append(cmd)
                return S.subprocess.CompletedProcess(cmd, 0)
            return real(cmd, *a, **kw)
        old_env = os.environ.get("REMOTE")
        os.environ["REMOTE"] = "dummy:remote"
        S.subprocess.run = fake
        try:
            self.run_sync("--upload")
        finally:
            S.subprocess.run = real
            if old_env is None:
                os.environ.pop("REMOTE", None)
            else:
                os.environ["REMOTE"] = old_env
        self.assertEqual([c[1] for c in calls], ["copy", "check"])
        for c in calls:
            self.assert_no_filter_with_files_from(c)
            lst = Path(c[c.index("--files-from") + 1]).read_text(encoding="utf-8").splitlines()
            self.assertNotIn("viewer/sync_config.js", lst)

    def assert_no_filter_with_files_from(self, cmd):
        self.assertIn("--files-from", cmd)
        bad = [x for x in cmd if x.split("=")[0] in (
            "--exclude", "--exclude-from", "--include", "--include-from", "--filter", "--filter-from",
            "--exclude-if-present")]
        self.assertEqual(bad, [], "rclone rejects filter flags combined with --files-from")

    # ------------------------------------------------------------ incremental export / upload
    def test_watermark_selection(self):
        import sync_export as SE
        fp = {"same": "5:100:9", "changed": "6:200:12", "deferred": "3:50:4", "new": "1:10:1"}
        wm = {"same": ("5:100:9", 0), "changed": ("5:100:9", 0), "deferred": ("3:50:4", 2), "gone": ("1:1:1", 0)}
        self.assertEqual(SE.select_cids(fp, wm), {"changed", "deferred", "new"})
        p = self.root / "wm.tsv"
        p.write_text("#last_full\t123\nsame\t5:100:9\t0\ndeferred\t3:50:4\t2\n", encoding="utf-8")
        self.assertEqual(SE.read_watermarks(p), {"same": ("5:100:9", 0, None), "deferred": ("3:50:4", 2, None)})

    def _att_db(self, src):
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE messages (id TEXT, conversationId TEXT, received_at INTEGER, sent_at INTEGER, "
                   "hasAttachments INTEGER)")
        db.execute("CREATE TABLE message_attachments (messageId TEXT, path TEXT, attachmentType TEXT, "
                   "editHistoryIndex INTEGER)")
        (src / "attachments.noindex" / "ab").mkdir(parents=True)
        return db

    def test_deferred_chat_not_reexported_until_attachment_appears(self):
        import time
        import sync_export as SE
        src = self.root / "src"
        db = self._att_db(src)
        now = int(time.time() * 1000)
        db.execute("INSERT INTO messages VALUES ('m1','big',5,?,1)", (now - 3600_000,))
        db.execute("INSERT INTO message_attachments VALUES ('m1','ab/f1','attachment',-1)")
        cur = db.cursor()
        un = SE.unavailable(cur, src, now - 86400_000)
        self.assertEqual(un, {"big": [("m1", now - 3600_000)]})
        fp = {"big": "1:5:1"}
        wm = {"big": ("1:5:1", 1, un["big"])}
        ready = lambda c, pend: SE.pending_ready(cur, src, pend, now)  # noqa: E731
        self.assertEqual(SE.select_cids(fp, wm, ready), set())        # file still missing: skip the chat
        (src / "attachments.noindex" / "ab" / "f1").write_bytes(b"x")
        self.assertEqual(SE.select_cids(fp, wm, ready), {"big"})      # downloaded now: re-export
        self.assertEqual(SE.unavailable(cur, src, now - 86400_000), {})
        # aged past DEFER_DAYS (+margin): re-export so the merge adds it with missing_kind
        old = [("m1", now - (SE.DEFER_DAYS + 2) * 86400_000)]
        (src / "attachments.noindex" / "ab" / "f1").unlink()
        self.assertTrue(SE.pending_ready(cur, src, old, now))
        self.assertFalse(SE.pending_ready(cur, src, [("m1", now - 3600_000)], now))
        self.assertTrue(SE.pending_ready(cur, src, None, now))        # pre-0.1.50 watermark row
        self.assertTrue(SE.pending_ready(cur, src, [("gone", now)], now))  # message deleted

    def test_pending_list_keeps_only_deferred_messages(self):
        items = [("a", 1_000_000), ("b", 2_000_000), ("c", 3_000_500)]
        self.assertEqual(S.match_pending(items, [3_000.0]), [("c", 3_000_500)])
        self.assertEqual(S.match_pending(items, []), items)          # no times: keep all
        self.assertEqual(S.match_pending(items, [99_999.0]), items)  # no match: keep all (safe)

    def test_watermarks_keep_pending_for_chats_not_exported(self):
        import sync_export as SE
        st = self.out / "_state"
        st.mkdir(parents=True, exist_ok=True)
        (st / S.WATERMARKS).write_text("#last_full\t5\nbig\t1:5:1\t1\tm1@100\nx\t1:1:1\t0\n", encoding="utf-8")
        tmp = self.root / "tmp"
        tmp.mkdir()
        (tmp / "_fingerprints.tsv").write_text("big\t1:5:1\nx\t2:2:2\nn\t1:9:9\n", encoding="utf-8")
        (tmp / "_selected.tsv").write_text("n\nx\n", encoding="utf-8")
        (tmp / "_unavail.tsv").write_text("n\tm9@200\n", encoding="utf-8")
        S.write_watermarks(st, tmp, set(), {"n": 1}, False)
        wm = SE.read_watermarks(st / S.WATERMARKS)
        self.assertEqual(wm["big"], ("1:5:1", 1, [("m1", 100)]))
        self.assertEqual(wm["x"], ("2:2:2", 0, None))
        self.assertEqual(wm["n"], ("1:9:9", 1, [("m9", 200)]))

    def test_cid_filter_cursor_filters_messages_query_in_sql(self):
        import sqlite3
        import sync_export as SE
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE messages (conversationId TEXT, json TEXT, sent_at INTEGER)")
        db.executemany("INSERT INTO messages VALUES (?,?,?)", [("a", "{}", 1), ("b", "BROKEN", 2), ("a", "{}", 3)])
        cur = SE.CidFilterCursor(db.cursor(), {"a"})
        cur.execute("SELECT\n conversationId,\n json\n FROM messages\n \n ORDER BY sent_at\n")
        rows = list(cur)
        self.assertEqual([r[0] for r in rows], ["a", "a"])  # b never read, so never JSON-parsed
        cur.execute("SELECT\n conversationId,\n json\n FROM messages\n WHERE sent_at >= 2\n ORDER BY sent_at\n")
        self.assertEqual(len(list(cur)), 1)
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 3)

    def test_no_change_run_skips_export(self):
        st = self.out / "_state"
        st.mkdir(parents=True, exist_ok=True)
        (st / S.WATERMARKS).write_text("#last_full\t%d\nc\t1:1:1\t0\n" % int(S.time.time()), encoding="utf-8")
        calls = []
        orig = (S.snapshot, S.run_precheck, S.run_export, S.sync)
        try:
            S.snapshot = lambda run, q: self.root / "src"
            S.run_precheck = lambda run, src, pre: calls.append("precheck") or set()
            S.run_export = lambda *a, **k: calls.append("export")
            S.sync = lambda *a, **k: calls.append("merge") or 0
            self.assertEqual(S.main(["--out", str(self.out)]), 0)
        finally:
            S.snapshot, S.run_precheck, S.run_export, S.sync = orig
        self.assertEqual(calls, ["precheck"])
        status = json.loads((st / "sync_status.json").read_text())
        self.assertEqual((status["state"], status["message"]), ("done", "no changes"))

    def test_deploy_viewer_when_repo_version_differs(self):
        repo = Path(__file__).resolve().parents[2]
        v = (repo / "VERSION").read_text(encoding="utf-8").strip()
        vd = self.out / "viewer"
        (vd / "themes").mkdir(parents=True)
        (vd / "VERSION").write_text("0.1.47 (build 25)\n", encoding="utf-8")
        (vd / "viewer.js").write_text("var CODE_VERSION='0.1.47 (build 25)';\n", encoding="utf-8")
        for keep in ("config.js", "sync_config.js", "themes/t.css"):
            (vd / keep).write_text("KEEP", encoding="utf-8")
        self.assertEqual(S.deploy_viewer(self.out, repo), v)
        self.assertEqual((vd / "VERSION").read_text(encoding="utf-8").strip(), v)
        self.assertIn("var CODE_VERSION='%s';" % v, (vd / "viewer.js").read_text(encoding="utf-8"))
        for keep in ("config.js", "sync_config.js", "themes/t.css"):
            self.assertEqual((vd / keep).read_text(encoding="utf-8"), "KEEP")
        self.assertIsNone(S.deploy_viewer(self.out, repo))  # same version: nothing copied
        # the deployed builder stamps the deployed version, so stamp and CODE_VERSION agree
        sys.path.insert(0, str(vd))
        try:
            import importlib
            import build_viewer as BV
            importlib.reload(BV)
            self.assertEqual(BV.read_version(vd), v)
        finally:
            sys.path.remove(str(vd))
            sys.modules.pop("build_viewer", None)

    def test_viewer_stale_only_when_stamp_is_newer(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        import re
        import subprocess
        js = (Path(__file__).resolve().parents[2] / "viewer" / "viewer.js").read_text(encoding="utf-8")
        fn = re.search(r"function verNewer\(a,b\)\{.*?return false\}", js, re.S).group(0)
        code = fn + ";console.log(JSON.stringify([verNewer('0.1.50 (build 28)','0.1.49 (build 27)')," \
                    "verNewer('0.1.49 (build 27)','0.1.50 (build 28)'),verNewer('0.1.50 (build 28)','0.1.50 (build 28)')," \
                    "verNewer('0.2.0','0.1.99')]))"
        r = subprocess.run([node, "-e", code], capture_output=True, text=True)
        self.assertEqual(json.loads(r.stdout), [True, False, False, True])

    def test_fingerprints_from_db(self):
        import sqlite3
        import sync_export as SE
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE messages (conversationId TEXT, received_at INTEGER)")
        db.executemany("INSERT INTO messages VALUES (?,?)", [("a", 1), ("a", 5), ("b", 3), (None, 9)])
        fp = SE.fingerprints(db.cursor())
        self.assertEqual(set(fp), {"a", "b"})
        self.assertTrue(fp["a"].startswith("2:5:"))
        before = fp["a"]
        db.execute("DELETE FROM messages WHERE received_at=1")  # a deletion changes the fingerprint
        self.assertNotEqual(SE.fingerprints(db.cursor())["a"], before)

    def test_full_export_decision_and_watermark_write(self):
        st = self.out / "_state"
        self.assertEqual(S.want_full_export(st, False), "no watermarks")
        tmp = self.root / "tmp"
        tmp.mkdir()
        (tmp / "_fingerprints.tsv").write_text("ok\t1:1:1\nbad\t2:2:2\ndef\t3:3:3\n", encoding="utf-8")
        self.assertEqual(S.write_watermarks(st, tmp, {"bad"}, {"def": 1}, True), 2)
        import sync_export as SE
        wm = SE.read_watermarks(st / S.WATERMARKS)
        self.assertEqual(wm, {"ok": ("1:1:1", 0, None), "def": ("3:3:3", 1, [])})  # failed one re-exported next time
        self.assertIsNone(S.want_full_export(st, False))
        self.assertEqual(S.want_full_export(st, True), "--full")
        self.assertEqual(S.want_full_export(st, False, now=S.time.time() + 8 * 86400), "weekly")
        S.write_watermarks(st, tmp, set(), {}, False)  # incremental keeps last_full
        self.assertIsNone(S.want_full_export(st, False))

    def test_glob_regex_like_rclone(self):
        ex = S.excluder(S.load_upload_excludes())
        for rel in ("viewer/sync_config.js", "_work/sync_tmp/A/data.json", "_state/sync.log", "x/y.pid",
                    "viewer/.build_tmp/data/1.js", "viewer/data.new/1.js"):
            self.assertTrue(ex(rel), rel)
        for rel in ("viewer/data/1.js", "001_A/A/data.json", "_state/conv_map.tsv", "Media/Pictures/x.jpg",
                    "_state/sync_watermarks.tsv"):
            self.assertFalse(ex(rel), rel)

    def test_files_from_list_has_changed_files_and_no_excluded(self):
        v = self.out / "viewer"
        (v / "data").mkdir(parents=True)
        (v / "data" / "1.js").write_text("same", encoding="utf-8")
        (v / "sync_config.js").write_text("token", encoding="utf-8")
        (self.out / "_work").mkdir()
        (self.out / "_work" / "junk.txt").write_text("x", encoding="utf-8")
        ex = S.excluder(S.load_upload_excludes())
        before = S.tree_state(self.out, ex)
        self.assertNotIn("viewer/sync_config.js", before)
        self.assertNotIn("_work/junk.txt", before)
        # rewritten with the same content (build_viewer swaps whole dirs): not uploaded
        os.utime(v / "data" / "1.js", ns=(1, 1))
        (v / "data" / "2.js").write_text("new", encoding="utf-8")
        (v / "sync_config.js").write_text("token2", encoding="utf-8")
        (self.out / "001_A" / "A" / "data.json").write_text(self.before + "{}\n", encoding="utf-8")
        files = S.changed_files(before, S.tree_state(self.out, ex))
        self.assertEqual(files, ["001_A/A/data.json", "viewer/data/2.js"])

    def test_upload_uses_files_from_with_changed_chat_only(self):
        write_chat(self.exp / "A", [msg("2026-01-01T10:00:00.000000", "A", "one"),
                                    msg("2026-01-03T10:00:00.000000", "A", "brand new")])
        (self.out / "viewer").mkdir()
        (self.out / "viewer" / "sync_config.js").write_text("token", encoding="utf-8")
        calls, lists, real = [], [], S.subprocess.run

        def fake(cmd, *a, **kw):
            if cmd and cmd[0] == "rclone":
                calls.append(cmd)
                lst = cmd[cmd.index("--files-from") + 1]
                lists.append(Path(lst).read_text(encoding="utf-8").splitlines())
                return S.subprocess.CompletedProcess(cmd, 0)
            if cmd and str(cmd[-1]).endswith("viewer") or "build_viewer.py" in " ".join(map(str, cmd)):
                return S.subprocess.CompletedProcess(cmd, 0)
            return real(cmd, *a, **kw)
        os.environ["REMOTE"] = "dummy:remote"
        S.subprocess.run = fake
        try:
            self.assertEqual(self.run_sync("--upload"), 0)
        finally:
            S.subprocess.run = real
            os.environ.pop("REMOTE", None)
        self.assertEqual([c[1] for c in calls], ["copy", "check"])
        for c in calls:
            self.assert_no_filter_with_files_from(c)
        self.assertIn("--no-traverse", calls[0])
        self.assertIn("--one-way", calls[1])
        files = lists[0]
        self.assertIn("001_A/A/data.json", files)
        self.assertIn("001_A/A/chat.md", files)
        self.assertNotIn("viewer/sync_config.js", files)
        self.assertNotIn("Media/Pictures/x.jpg", files)  # unchanged
        self.assertFalse(any(f.startswith("_work/") or f.endswith(".log") for f in files))
        log = (self.out / "_state" / "sync.log").read_text(encoding="utf-8")
        self.assertRegex(log, r"step=merge secs=\d+\.\d")
        self.assertRegex(log, r"step=upload secs=\d+\.\d")

    def test_upload_files_refuses_excluded_path(self):
        run = S.Run.__new__(S.Run)
        run.out, run.state = self.out, self.out / "_state"
        called, real = [], S.subprocess.run
        S.subprocess.run = lambda cmd, *a, **kw: called.append(cmd)
        try:
            with self.assertRaises(SystemExit):
                S.upload_files(run, "dummy:remote", ["001_A/A/data.json", "viewer/sync_config.js"],
                               S.load_upload_excludes())
        finally:
            S.subprocess.run = real
        self.assertEqual(called, [])

    @unittest.skipUnless(shutil.which("rclone"), "rclone not installed")
    def test_upload_files_real_rclone_local_remote(self):
        """Runs the actual rclone CLI (copy + check with --files-from) against a local dir remote,
        so a flag combination rclone rejects fails here instead of in the nightly sync."""
        (self.out / "viewer").mkdir()
        (self.out / "viewer" / "sync_config.js").write_text("token", encoding="utf-8")
        (self.out / "viewer" / "index.html").write_text("<html>", encoding="utf-8")
        dest = self.root / "remote"
        dest.mkdir()
        run = S.Run.__new__(S.Run)
        run.out, run.state = self.out, self.out / "_state"
        files = ["001_A/A/data.json", "Media/Pictures/x.jpg", "viewer/index.html"]
        rc, crc = S.upload_files(run, str(dest), files, S.load_upload_excludes())
        self.assertEqual((rc, crc), (0, 0), (self.out / "_state" / "upload_check.log").read_text(errors="replace"))
        got = sorted(str(p.relative_to(dest)).replace(os.sep, "/") for p in dest.rglob("*") if p.is_file())
        self.assertEqual(got, sorted(files))
        self.assertFalse((dest / "viewer" / "sync_config.js").exists())


if __name__ == "__main__":
    unittest.main()
