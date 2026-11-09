import json
import os
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
        # the real --upload path passes the exclude to both rclone copy and rclone check
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
            self.assertIn("viewer/sync_config.js", [c[i + 1] for i, x in enumerate(c) if x == "--exclude"])

    # ------------------------------------------------------------ incremental export / upload
    def test_watermark_selection(self):
        import sync_export as SE
        fp = {"same": "5:100:9", "changed": "6:200:12", "deferred": "3:50:4", "new": "1:10:1"}
        wm = {"same": ("5:100:9", 0), "changed": ("5:100:9", 0), "deferred": ("3:50:4", 2), "gone": ("1:1:1", 0)}
        self.assertEqual(SE.select_cids(fp, wm), {"changed", "deferred", "new"})
        p = self.root / "wm.tsv"
        p.write_text("#last_full\t123\nsame\t5:100:9\t0\ndeferred\t3:50:4\t2\n", encoding="utf-8")
        self.assertEqual(SE.read_watermarks(p), {"same": ("5:100:9", 0), "deferred": ("3:50:4", 2)})

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
        self.assertEqual(wm, {"ok": ("1:1:1", 0), "def": ("3:3:3", 1)})  # failed one re-exported next time
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


if __name__ == "__main__":
    unittest.main()
