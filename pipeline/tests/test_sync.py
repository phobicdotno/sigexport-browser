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

    def test_new_chat_gets_next_nnn_and_dry_run(self):
        write_chat(self.exp / "Zed", [msg("2026-02-01T10:00:00.000000", "Zed", "hi")])
        (self.exp / "_cids.tsv").write_text("cid-z\tZed\n")
        self.assertEqual(self.run_sync("--dry-run"), 0)
        self.assertFalse((self.out / "002_Zed").exists())
        self.assertEqual(self.run_sync(), 0)
        self.assertTrue((self.out / "002_Zed" / "Zed" / "data.json").is_file())
        self.assertIn("2\tZed\tcid-z", (self.out / "_state" / "id_map.tsv").read_text())
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

    def test_matcher_rules(self):
        old = [msg("2026-01-01T10:00:00.123000", "A", "x", server_ts=5)]
        self.assertEqual(S.new_messages(old, [msg("2026-01-01T10:00:00.123", "A", "edited")]), [])
        self.assertEqual(S.new_messages(old, [msg("2026-01-01T10:00:59", "A", "x")]), [])
        self.assertEqual(len(S.new_messages(old, [msg("2026-01-01T10:01:30", "A", "x")])), 1)
        self.assertEqual(S.new_messages(old, [msg("2026-01-01T11:00:00", "A", "y", server_ts=5)]), [])


if __name__ == "__main__":
    unittest.main()
