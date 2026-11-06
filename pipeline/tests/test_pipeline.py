"""Synthetic-data tests for the cross-platform pipeline. Run: python3 -m unittest discover pipeline/tests"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPE))
import export_all  # noqa: E402
import platform_util as pu  # noqa: E402

DD = PIPE / "dedup.py"


def dd(mode, root, d, idx, env=None):
    p = subprocess.run([sys.executable, "-I", str(DD), mode, str(root), d, str(idx)],
                       capture_output=True, encoding="utf-8", env={**os.environ, **(env or {})})
    return p.returncode, json.loads(p.stdout)


def mkchat(root, d, chat, files, sep="/", line_per_file=False):
    c = Path(root, d, chat)
    (c / "media").mkdir(parents=True)
    links = "".join(f"![{f}](./media{sep}{f})  " for f in files)
    md = "".join(f"[2026-01-01 10:00:00] T: ![{f}](./media{sep}{f})  \n" for f in files) if line_per_file \
        else f"[2026-01-01 10:00:00] T: {links}\n"
    (c / "chat.md").write_text(md, encoding="utf-8")
    (c / "index.html").write_text("".join(f'<img src="./media{sep}{f}">\n' for f in files), encoding="utf-8")
    js = "media\\\\" if sep == "\\" else "media/"
    (c / "data.json").write_text(json.dumps({"attachments": []}) + "\n" + "".join(
        '{"attachments": [{"name": "%s", "path": "%s%s", "missing_kind": null}]}\n' % (f, js, f) for f in files),
        encoding="utf-8")
    return c


class DedupLinks(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())
        self.root, self.idx = self.t / "root", self.t / "state" / "media_index.tsv"
        self.idx.parent.mkdir(parents=True)
        self.idx.touch()

    def test_several_links_per_line_and_forward_slashes(self):
        a = mkchat(self.root, "001_A", "Alpha", ["x.jpg", "y.jpg"])
        (a / "media/x.jpg").write_bytes(b"same")
        (a / "media/y.jpg").write_bytes(b"same2")
        b = mkchat(self.root, "002_B", "Beta", ["p.jpg", "q.jpg", "r.jpg"])
        (b / "media/p.jpg").write_bytes(b"same")
        (b / "media/q.jpg").write_bytes(b"other")
        (b / "media/r.jpg").write_bytes(b"same2")
        dd("dedup", self.root, "001_A", self.idx)
        rc, res = dd("dedup", self.root, "002_B", self.idx)
        self.assertEqual(res["deduped"], 2)
        md = (b / "chat.md").read_text(encoding="utf-8")
        self.assertIn("](../../001_A/Alpha/media/x.jpg)", md)
        self.assertIn("](./media/q.jpg)", md)
        self.assertIn("](../../001_A/Alpha/media/y.jpg)", md)
        self.assertNotIn("\\", md)
        self.assertNotIn("\\", self.idx.read_text(encoding="utf-8"))
        self.assertEqual(dd("linkcheck", self.root, "002_B", self.idx)[0], 0)

    def test_windows_backslash_links_are_normalised(self):
        a = mkchat(self.root, "001_A", "Alpha", ["x.jpg"])
        (a / "media/x.jpg").write_bytes(b"dup")
        b = mkchat(self.root, "002_B", "Beta", ["p.jpg", "k.jpg"], sep="\\")
        (b / "media/p.jpg").write_bytes(b"dup")
        (b / "media/k.jpg").write_bytes(b"keep")
        dd("dedup", self.root, "001_A", self.idx)
        dd("dedup", self.root, "002_B", self.idx)
        for f in ("chat.md", "index.html", "data.json"):
            t = (b / f).read_text(encoding="utf-8")
            self.assertNotIn("media\\", t, f)
            self.assertIn("001_A/Alpha/media/x.jpg", t, f)
        self.assertIn('"path": "media/k.jpg"', (b / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(dd("linkcheck", self.root, "002_B", self.idx)[0], 0)

    def test_linkcheck_ignores_reported_missing(self):
        b = mkchat(self.root, "002_B", "Beta", ["b1.jpg", "gone.jpg"], line_per_file=True)
        (b / "media/b1.jpg").write_bytes(b"x")
        with open(b / "data.json", "a", encoding="utf-8") as f:
            f.write('{"attachments": [{"name": "", "path": "", "missing_kind": "media"}]}\n')
        log = self.t / "sig.out"
        log.write_text("No file to copy at /x/gone, skipping!\n", encoding="utf-8")
        dd("dedup", self.root, "002_B", self.idx)
        rc, lc = dd("linkcheck", self.root, "002_B", self.idx, {"LC_SIGLOG": str(log)})
        self.assertEqual(rc, 0, lc)
        self.assertEqual((lc["missing"], lc["missing_reported"], lc["no_file_lines"]), (0, 2, 1))
        (b / "media/b1.jpg").unlink()  # real breakage still fails
        self.assertEqual(dd("linkcheck", self.root, "002_B", self.idx)[0], 1)


class Names(unittest.TestCase):
    def test_windows_illegal_and_reserved(self):
        for n in ['a<b>c:"d"/e\\f|g?h*', "CON", "nul", "Com1", "   ", "", "Åse Ørn", "a.", "😀"]:
            s = pu.safe_component(n)
            self.assertIsNone(pu.windows_problem(s), (n, s))
            self.assertRegex(s, r"^[A-Za-z0-9_]+$")
            self.assertLessEqual(len(s), 41)
        self.assertEqual(export_all.chat_dir_name(7, "Alpha Test"), "007_Alpha_Test")
        self.assertEqual(pu.windows_problem("CON"), "reserved name")
        self.assertEqual(pu.windows_problem("a:b"), "illegal character")

    def test_unique_names_and_list_parse(self):
        names = export_all.parse_list_chats("Alice | Alice | Bob, Jr | Carol\n")
        self.assertEqual(export_all.unique_names(names), (["Alice", "Bob, Jr", "Carol"], ["Alice"]))

    def test_collisions(self):
        c = export_all.find_collisions(3, ["Alice", "Alice2"], {1: ["Alice"], 2: ["Bob"]})
        kinds = [k for k, _ in c]
        self.assertIn("multi-match", kinds)
        self.assertIn("same-folder", kinds)

    def test_signal_dirs(self):
        h = Path("/h")
        self.assertEqual(pu.signal_dir_candidates("darwin", {}, h)[0], h / "Library/Application Support/Signal")
        lin = pu.signal_dir_candidates("linux", {}, h)
        self.assertIn(h / ".var/app/org.signal.Signal/config/Signal", lin)
        self.assertEqual(lin[0], h / ".config/Signal")
        self.assertEqual(pu.signal_dir_candidates("win32", {"APPDATA": "C:/U/AppData/Roaming"}, h)[0],
                         Path("C:/U/AppData/Roaming/Signal"))


FAKE = textwrap.dedent('''\
    import sys, pathlib, os
    a = sys.argv[1:]
    if "--list-chats" in a:
        print("Alice | Alice | Bob | Carol"); sys.exit(0)
    chat = a[a.index("--chats") + 1]; dest = pathlib.Path(a[-1])
    calls = pathlib.Path(os.environ["FAKE_CALLS"]); calls.open("a", encoding="utf-8").write(chat + "\\n")
    if chat == os.environ.get("FAKE_FAIL"): sys.exit(1)
    folders = {"Alice": ["Alice", "Alice2"], "Bob": ["Bob"], "Carol": ["Bob"]}[chat]
    for f in folders:
        m = dest / f / "media"; m.mkdir(parents=True)
        (m / "pic.jpg").write_bytes(b"shared")
        (dest / f / "chat.md").write_text("[2026] T: ![pic.jpg](./media/pic.jpg)  \\n", encoding="utf-8")
''')


@unittest.skipIf(sys.platform == "win32", "fake sigexport uses a shebang script")
class Resume(unittest.TestCase):
    def test_resume_and_collisions(self):
        t = Path(tempfile.mkdtemp())
        src = t / "src"
        src.mkdir()
        (src / "config.json").write_text("{}", encoding="utf-8")
        fake = t / "sigexport"
        fake.write_text(f"#!{sys.executable}\n" + FAKE, encoding="utf-8")
        fake.chmod(0o755)
        calls = t / "calls.txt"
        os.environ.update(FAKE_CALLS=str(calls), FAKE_FAIL="Bob", POLL_SECS="0.05",
                          MIN_FREE_GIB="0", ABORT_FREE_GIB="0")
        args = ["--src", str(src), "--out", str(t / "out"), "--sigexport", str(fake), "--remote", "", "--media-layout", "per-chat"]
        try:
            self.assertEqual(export_all.main(args), 0)
            st = t / "out/_state"
            self.assertEqual((st / "chats.txt").read_text(encoding="utf-8"), "Alice\nBob\nCarol\n")
            self.assertEqual(calls.read_text(encoding="utf-8").split(), ["Alice", "Bob", "Carol"])
            coll = (st / "collisions.tsv").read_text(encoding="utf-8")
            self.assertIn("duplicate-name", coll)
            self.assertIn("multi-match", coll)
            self.assertIn("2\tBob\texport failed", (st / "failed.txt").read_text(encoding="utf-8"))
            self.assertIn("](../Alice/media/pic.jpg)",
                          (t / "out/001_Alice/Alice2/chat.md").read_text(encoding="utf-8"))
            # resume: nothing re-exported, failed chat stays skipped
            calls.unlink()
            self.assertEqual(export_all.main(args), 0)
            self.assertFalse(calls.exists())
            # retry failed chat after clearing failed.txt
            (st / "failed.txt").write_text("", encoding="utf-8")
            os.environ["FAKE_FAIL"] = ""
            export_all.main(args)
            self.assertEqual(calls.read_text(encoding="utf-8").split(), ["Bob"])
            self.assertIn("same-folder\tBob also exported by index 3",
                          (st / "collisions.tsv").read_text(encoding="utf-8"))
            man = (st / "manifest.csv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(man[0], export_all.MAN_HEADER)
        finally:
            for k in ("FAKE_CALLS", "FAKE_FAIL", "POLL_SECS", "MIN_FREE_GIB", "ABORT_FREE_GIB"):
                os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main()
