"""Synthetic tests for prune_signal.py + the sync.py prune gate (no real Signal data)."""
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prune_signal as P  # noqa: E402
import sync as S  # noqa: E402

try:
    from Crypto.Cipher import AES
    HAVE_AES = True
except ImportError:  # pragma: no cover
    HAVE_AES = False

NOW = 1_800_000_000_000
DAY = P.DAY_MS


def encrypt(plain: bytes, keys: bytes) -> bytes:
    ck, mk = keys[:32], keys[32:]
    iv = os.urandom(16)
    pad = 16 - len(plain) % 16
    body = AES.new(ck, AES.MODE_CBC, iv).encrypt(plain + bytes([pad]) * pad)
    return iv + body + hmac.new(mk, iv + body, hashlib.sha256).digest()


SCHEMA = """CREATE TABLE message_attachments (messageId TEXT, editHistoryIndex INTEGER, attachmentType TEXT,
 orderInMessage INTEGER, sentAt INTEGER, receivedAtMs INTEGER, size INTEGER, path TEXT, plaintextHash TEXT,
 localKey TEXT, thumbnailPath TEXT, screenshotPath TEXT, backupThumbnailPath TEXT)"""


@unittest.skipUnless(HAVE_AES, "pycryptodome not installed")
class PruneTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        r = Path(self.td.name)
        self.sig, self.out = r / "Signal", r / "OUT"
        self.att = self.sig / "attachments.noindex"
        (self.out / "_state").mkdir(parents=True)
        (self.out / "Media" / "Pictures").mkdir(parents=True)
        self.db = sqlite3.connect(":memory:")
        self.db.execute(SCHEMA)
        self.index, self.remote = [], {}

    def tearDown(self):
        self.td.cleanup()

    def add(self, name, age_days, archived=True, on_remote=True, atype="attachment", sha=True, plain=None):
        plain = plain or (name.encode() * 50)
        keys = os.urandom(64)
        rel = f"{hashlib.sha256(name.encode()).hexdigest()[:2]}/{hashlib.sha256(name.encode()).hexdigest()}"
        (self.att / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.att / rel).write_bytes(encrypt(plain, keys))
        md5 = hashlib.md5(plain).hexdigest()
        arc = f"Media/Pictures/{name}.jpg"
        if archived:
            (self.out / arc).write_bytes(plain)
            self.index.append(f"{md5}\t{arc}")
            if on_remote:
                self.remote[arc] = md5
        t = NOW - age_days * DAY
        self.db.execute("INSERT INTO message_attachments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (name, -1, atype, 0, t, t, len(plain), rel,
                         hashlib.sha256(plain).hexdigest() if sha else None,
                         base64.b64encode(keys).decode(), None, None, None))
        return rel

    def run_prune(self, dry=False):
        (self.out / "_state" / "media_index.tsv").write_text("\n".join(self.index) + "\n", encoding="utf-8")
        return P.prune(self.db.cursor(), self.sig, self.out, 30, self.remote, dry, now_ms=NOW)

    def test_nfd_archive_name_matches_nfc_remote_and_only_needed_paths_fetched(self):
        import unicodedata
        nfd = unicodedata.normalize("NFD", "bilde_\u00e6\u00f8\u00e5")
        rel = self.add(nfd, 60)
        self.add("nyere", 5)
        arc = f"Media/Pictures/{nfd}.jpg"
        nfc_remote = {unicodedata.normalize("NFC", k): v for k, v in self.remote.items()}
        self.assertNotIn(arc, nfc_remote)  # literal lookup would miss it
        asked = []

        def fetch(arcs):
            asked.append(set(arcs))
            return {k: v for k, v in nfc_remote.items() if unicodedata.normalize("NFC", k) in
                    {unicodedata.normalize("NFC", a) for a in arcs}}
        (self.out / "_state" / "media_index.tsv").write_text("\n".join(self.index) + "\n", encoding="utf-8")
        s = P.prune(self.db.cursor(), self.sig, self.out, 30, fetch, False, now_ms=NOW)
        self.assertEqual(s["pruned_files"], 1)
        self.assertFalse((self.att / rel).exists())
        self.assertEqual(asked, [{arc}])  # only the candidate's archive counterpart

    def test_parse_md5sum_normalises_to_nfc(self):
        import unicodedata
        line = "0" * 32 + "  " + unicodedata.normalize("NFD", "Pictures/\u00e6\u00f8\u00e5.jpg")
        self.assertEqual(P.parse_md5sum(line, "Media/"),
                         {unicodedata.normalize("NFC", "Media/Pictures/\u00e6\u00f8\u00e5.jpg"): "0" * 32})

    def test_old_verified_deleted_new_and_unverified_kept(self):
        old = self.add("old", 60)
        new = self.add("new", 5)
        unarch = self.add("unarchived", 60, archived=False)
        noremote = self.add("noremote", 60, on_remote=False)
        sticker = self.add("sticker", 60, atype="sticker")
        s = self.run_prune()
        self.assertFalse((self.att / old).exists())
        self.assertTrue((Path(s["staging"]) / old).is_file())
        for rel in (new, unarch, noremote, sticker):
            self.assertTrue((self.att / rel).is_file(), rel)
        self.assertEqual(s["pruned_files"], 1)
        self.assertEqual(s["pruned_bytes"], len((Path(s["staging"]) / old).read_bytes()))
        self.assertEqual(s["kept"], {"not_in_index": 1, "not_on_remote": 1})
        self.assertNotIn("old", json.dumps(s).replace(s["staging"], ""))

    def test_dry_run_deletes_nothing(self):
        old = self.add("old", 60)
        s = self.run_prune(dry=True)
        self.assertTrue((self.att / old).is_file())
        self.assertEqual((s["pruned_files"], s["staging"]), (1, None))

    def test_corrupt_and_mismatch_kept(self):
        bad = self.add("corrupt", 60)
        b = bytearray((self.att / bad).read_bytes()); b[20] ^= 1
        (self.att / bad).write_bytes(bytes(b))
        arcmod = self.add("arcmod", 60)
        (self.out / "Media/Pictures/arcmod.jpg").write_bytes(b"changed")
        rmod = self.add("rmod", 60)
        self.remote["Media/Pictures/rmod.jpg"] = "0" * 32
        s = self.run_prune()
        for rel in (bad, arcmod, rmod):
            self.assertTrue((self.att / rel).is_file())
        self.assertEqual(s["pruned_files"], 0)
        self.assertEqual(s["kept"], {"decrypt_failed": 1, "archive_md5_mismatch": 1, "remote_md5_mismatch": 1})

    def test_path_shared_with_newer_or_other_row_kept(self):
        rel = self.add("shared", 60)
        self.db.execute("INSERT INTO message_attachments (messageId, editHistoryIndex, attachmentType, orderInMessage,"
                        " sentAt, receivedAtMs, size, path, localKey) SELECT 'fwd', -1, attachmentType, 0, ?, ?, size,"
                        " path, localKey FROM message_attachments WHERE messageId='shared'", (NOW, NOW))
        rel2 = self.add("quoted", 60)
        rel3 = self.add("thumbof", 60)
        self.db.execute("UPDATE message_attachments SET thumbnailPath=? WHERE messageId='quoted'", (rel3,))
        self.db.execute("INSERT INTO message_attachments (messageId, editHistoryIndex, attachmentType, orderInMessage,"
                        " sentAt, size, path, localKey) VALUES ('q', -1, 'quote', 0, 0, 1, ?, 'k')", (rel2,))
        s = self.run_prune()
        self.assertTrue((self.att / rel).is_file())
        self.assertTrue((self.att / rel2).is_file())
        self.assertTrue((self.att / rel3).is_file())
        self.assertEqual((s["candidates"], s["pruned_files"]), (0, 0))

    def test_sha256_mismatch_kept(self):
        rel = self.add("sha", 60)
        self.db.execute("UPDATE message_attachments SET plaintextHash=? WHERE messageId='sha'", ("ab" * 32,))
        s = self.run_prune()
        self.assertTrue((self.att / rel).is_file())
        self.assertEqual(s["kept"], {"sha256_mismatch": 1})


class GateTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.run_ = S.Run(Path(self.td.name), False)
        self.calls = []

    def tearDown(self):
        self.td.cleanup()

    def runner(self, run, days, remote, dry):
        self.calls.append((days, dry))
        return {"pruned_files": 2, "pruned_bytes": 10, "candidates": 3, "kept": {}, "staging": None}

    def test_no_prune_when_upload_check_failed(self):
        self.run_.upload_ok = False
        self.assertIsNone(S.maybe_prune(self.run_, 30, True, 1, "r:x", runner=self.runner))
        self.assertEqual(self.calls, [])

    def test_no_prune_without_upload_or_when_off(self):
        self.run_.upload_ok = True
        self.assertIsNone(S.maybe_prune(self.run_, 30, False, 0, "r:x", runner=self.runner))
        self.assertIsNone(S.maybe_prune(self.run_, 0, True, 0, "r:x", runner=self.runner))
        self.assertEqual(self.calls, [])

    def test_no_prune_when_sync_failed(self):
        self.run_.upload_ok = True
        self.assertIsNone(S.maybe_prune(self.run_, 30, True, 1, "r:x", runner=self.runner))
        self.assertEqual(self.calls, [])

    def test_prune_after_verified_upload_counts_in_status(self):
        self.run_.upload_ok = True
        S.maybe_prune(self.run_, 30, True, 0, "r:x", runner=self.runner)
        self.assertEqual(self.calls, [(30, False)])
        st = json.loads((Path(self.td.name) / "_state" / "sync_status.json").read_text())
        self.assertEqual((st["pruned_files"], st["pruned_bytes"]), (2, 10))

    def test_staging_removed(self):
        st = Path(self.td.name) / "_work" / "pruned" / "2026-01-01" / "aa"
        st.mkdir(parents=True)
        S.drop_staging({"staging": str(st.parent)})
        self.assertFalse(st.parent.exists())


if __name__ == "__main__":
    unittest.main()
