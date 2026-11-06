"""Central media layout, crash resume, disk guard and chats.txt order check (synthetic data)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPE))
import central_media as CM  # noqa: E402
import export_all  # noqa: E402


def mkchat(root, top, chat, files):
    """files: {name: bytes}; refs in chat.md, index.html and data.json like sigexport writes them."""
    c = Path(root, top, chat)
    (c / "media").mkdir(parents=True)
    for fn, data in files.items():
        (c / "media" / fn).write_bytes(data)
    from urllib.parse import quote
    import html
    (c / "chat.md").write_text("".join(f"[2026-01-01 10:00] T: ![{f}](./media/{quote(f)})  \n" for f in files)
                               + "[2026-01-01 10:01] T: ![gone](./media/gone.jpg)  \n", encoding="utf-8")
    (c / "index.html").write_text("".join(f'<img src="{html.escape("./media/" + quote(f))}">\n' for f in files),
                                  encoding="utf-8")
    (c / "data.json").write_text("".join(json.dumps({"date": "2026-01-01 10:00", "body": "",
                                 "attachments": [{"name": f, "path": "media/" + f}]}, ensure_ascii=False) + "\n"
                                 for f in files), encoding="utf-8")
    return c


class CentralTest(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.root = self.t / "out"
        self.state = self.root / "_state"
        self.state.mkdir(parents=True)
        (self.state / "media_index.tsv").touch()
        self.c = CM.Central(str(self.root), str(self.state))

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def index(self):
        return CM.load_index(str(self.state / "media_index.tsv"))

    def test_new_and_known_md5_and_clash(self):
        a = mkchat(self.root, "001_A", "A", {"x y.jpg": b"one", "clip#1.mp4": b"vid", "v.ogg": b"aud"})
        res = self.c.run("001_A")
        self.assertEqual(res["moved"], 3)
        self.assertTrue((self.root / "Media/Pictures/x y.jpg").is_file())
        self.assertTrue((self.root / "Media/Videos/clip#1.mp4").is_file())
        self.assertTrue((self.root / "Media/Audio/v.ogg").is_file())
        self.assertFalse((a / "media").exists())
        md = (a / "chat.md").read_text(encoding="utf-8")
        self.assertIn("](../../Media/Pictures/x%20y.jpg)", md)
        self.assertIn("](../../Media/Videos/clip%231.mp4)", md)
        self.assertIn("](./media/gone.jpg)", md)  # sigexport-reported missing stays as is
        self.assertIn('"path": "../../Media/Pictures/x y.jpg"', (a / "data.json").read_text(encoding="utf-8"))
        self.assertIn('src="../../Media/Videos/clip%231.mp4"', (a / "index.html").read_text(encoding="utf-8"))
        # chat 2: known md5 -> link, delete copy; same name but different content -> suffix
        b = mkchat(self.root, "002_B", "B", {"dup.jpg": b"one", "x y.jpg": b"two"})
        res = self.c.run("002_B")
        self.assertEqual((res["deduped"], res["moved"]), (1, 1))
        import hashlib
        suf = hashlib.md5(b"two").hexdigest()[:8]
        self.assertTrue((self.root / f"Media/Pictures/x y_{suf}.jpg").is_file())
        md = (b / "chat.md").read_text(encoding="utf-8")
        self.assertIn("](../../Media/Pictures/x%20y.jpg)", md)  # dup.jpg -> first copy
        self.assertIn(f"](../../Media/Pictures/x%20y_{suf}.jpg)", md)
        self.assertEqual((self.root / "Media/Pictures/x y.jpg").read_bytes(), b"one")
        self.assertFalse((b / "media").exists())
        self.assertEqual(sorted(self.index().values()), sorted(
            ["Media/Pictures/x y.jpg", "Media/Videos/clip#1.mp4", "Media/Audio/v.ogg",
             f"Media/Pictures/x y_{suf}.jpg"]))
        self.assertEqual(self.c.pending(), [])
        self.assertFalse((self.root / "_work/backup/002_B").exists())

    def test_stickers_go_to_pictures(self):
        mkchat(self.root, "001_A", "A", {"doc.pdf": b"pdf"})
        s = self.root / "001_A" / "exported_stickers"
        s.mkdir()
        (s / "st1.webp").write_bytes(b"stk")
        (s / "st2").write_bytes(b"stk2")
        self.c.run("001_A")
        self.assertTrue((self.root / "Media/Pictures/st1.webp").is_file())
        self.assertTrue((self.root / "Media/Pictures/st2").is_file())
        self.assertTrue((self.root / "Media/Documents/doc.pdf").is_file())

    def test_link_check_failure_restores_backup(self):
        a = mkchat(self.root, "001_A", "A", {"p.jpg": b"pp"})
        before = (a / "chat.md").read_text(encoding="utf-8")
        with mock.patch.object(CM, "rewrite_chat", return_value=0):  # rewrite "forgets" refs
            with self.assertRaises(CM.CentralError):
                self.c.run("001_A")
        self.assertEqual((a / "chat.md").read_text(encoding="utf-8"), before)
        self.assertEqual((a / "media/p.jpg").read_bytes(), b"pp")
        self.assertFalse((self.root / "Media/Pictures/p.jpg").exists())
        self.assertEqual(self.index(), {})
        self.assertEqual(self.c.pending(), [])

    def test_resume_after_crash_mid_chat(self):
        a = mkchat(self.root, "001_A", "A", {"p.jpg": b"pp", "q.jpg": b"qq", "r.mp4": b"rr"})
        real_rename = os.rename
        calls = []

        def crash_rename(s, d):
            calls.append(s)
            if len(calls) == 2:
                raise KeyboardInterrupt("power cut")  # not caught -> no rollback
            return real_rename(s, d)
        with mock.patch.object(CM.os, "rename", crash_rename):
            with self.assertRaises(KeyboardInterrupt):
                self.c.run("001_A")
        self.assertEqual(self.c.pending(), ["001_A"])
        self.assertTrue((self.root / "_work/backup/001_A").is_dir())
        res = self.c.resume("001_A")
        self.assertEqual(res["moved"], 3)
        for f in ("Pictures/p.jpg", "Pictures/q.jpg", "Videos/r.mp4"):
            self.assertTrue((self.root / "Media" / f).is_file(), f)
        self.assertEqual(len(self.index()), 3)
        self.assertEqual(len((self.state / "media_index.tsv").read_text().splitlines()), 3)
        self.assertFalse((a / "media").exists())
        self.assertEqual(self.c.pending(), [])

    def test_order_check(self):
        (self.root / "001_Alpha").mkdir()
        (self.root / "_dupe_chats" / "003_Gamma").mkdir(parents=True)
        self.assertEqual(CM.verify_order(str(self.root), ["Alpha", "Beta", "Gamma"]), [])
        self.assertEqual(CM.verify_order(str(self.root), ["Alpha", "Gamma", "Beta"]), ["003"])
        self.assertEqual(CM.verify_order(str(self.root), ["Alpha"]), ["003"])


class DiskGuardTest(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.root = self.t / "out"
        a = SimpleNamespace(src=str(self.t), out=str(self.root), remote="", min_free="8", abort_free="3",
                            transfers="2", sigexport=sys.executable, state=None, media_layout="central")
        self.p = export_all.Pipeline(a)
        self.p.state.mkdir(parents=True)
        self.p.disk_wait = 0
        self.p.poll = 0.05

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    @staticmethod
    def usage(free_gib):
        return SimpleNamespace(total=1 << 40, used=0, free=int(free_gib * (1 << 30)))

    def test_estimate_sources_and_need(self):
        self.assertEqual(self.p.need_gib(5), (8.0, "no estimate"))
        (self.p.state / "hashes.tsv").write_text("005_X/X/media/a\tm\t%d\n006_Y/Y/media/b\tm\t1\n" % (10 << 30))
        self.assertEqual(self.p.estimate_bytes(5), 10 << 30)
        export_all.append(self.p.bytes_p, f"5\t{4 << 30}")
        self.assertEqual(self.p.estimate_bytes(5), 4 << 30)  # chat_bytes.tsv wins
        self.assertAlmostEqual(self.p.need_gib(5)[0], 4 * 1.15 + 3)

    def test_chat_that_will_not_fit_is_skipped_for_retry(self):
        export_all.append(self.p.bytes_p, f"7\t{20 << 30}")
        with mock.patch("shutil.disk_usage", return_value=self.usage(10)), \
                mock.patch.object(self.p, "export_chat") as ex:
            self.p.process(7, "Chat")
        ex.assert_not_called()
        self.assertIn("skipped-lowdisk", self.p.retry_p.read_text())
        self.assertFalse(self.p.failed_p.exists() and self.p.failed_p.read_text())

    def fake_popen(self):
        fake = self.t / "sigexport_fake.py"
        fake.write_text("import time\ntime.sleep(60)\n")
        real = subprocess.Popen
        self.procs = []

        def popen(cmd, **kw):
            pr = real([sys.executable, str(fake)], **kw)
            self.procs.append(pr)
            return pr
        return mock.patch.object(export_all.subprocess, "Popen", side_effect=popen)

    def test_export_chat_poll_kills_on_floor(self):
        """The real export_chat poll loop stops a hanging 'sigexport' at the hard floor."""
        frees = iter([20, 2.0] + [2.0] * 1000)
        with mock.patch("shutil.disk_usage", side_effect=lambda *_: self.usage(next(frees))), self.fake_popen():
            rc = self.p.export_chat("Big", self.root / "009_Big")
        self.assertEqual(rc, 2)
        self.assertIsNotNone(self.procs[0].returncode)

    def test_hard_floor_marks_retry(self):
        frees = iter([20, 20, 2.5] + [2.5] * 1000)
        with mock.patch("shutil.disk_usage", side_effect=lambda *_: self.usage(next(frees))), self.fake_popen():
            self.p.process(9, "Big")
        self.assertIn("low disk abort", self.p.retry_p.read_text())
        self.assertIn("aborted-lowdisk", self.p.man_p.read_text())
        self.assertFalse(self.p.failed_p.exists() and self.p.failed_p.read_text())


if __name__ == "__main__":
    unittest.main()
