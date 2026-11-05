"""Builds the viewer against tests/fixtures/export (synthetic names only) and checks the output
is portable: '/'-separated, percent-encoded relative URLs that all resolve to real files."""
import json, re, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / 'tests' / 'fixtures' / 'export'


def payload(js):
    return json.loads(js[js.index('=', js.index(']=') if ']=' in js else 0) + 1:].rstrip().rstrip(';'))


class BuildViewerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / 'export'
        shutil.copytree(FIXTURE, self.root)
        self.out = self.root / 'viewer'
        shutil.copytree(REPO / 'viewer', self.out)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self):
        r = subprocess.run([sys.executable, '-I', str(self.out / 'build_viewer.py'), '--root', str(self.root),
                            '--out', str(self.out)], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def chats(self):
        res = {}
        for f in sorted((self.out / 'data').glob('*.js')):
            res[f.name] = payload(f.read_text(encoding='utf-8'))
        return res

    def test_build_paths_portable_and_resolve(self):
        stdout = self.build()
        self.assertIn('chats=2', stdout)
        self.assertIn('unresolved_media=0', stdout)
        idx = payload((self.out / 'index_data.js').read_text(encoding='utf-8'))
        self.assertEqual(sorted(c['name'] for c in idx), ['Claude & Siri chat', 'Siri'])
        chats = self.chats()
        self.assertEqual(len(chats), 2)
        self.assertEqual(len(list((self.out / 'search').glob('*.js'))), 2)
        paths = []
        for chat in chats.values():
            self.assertNotIn('\\', chat['dir'])
            for m in chat['messages']:
                for a in m['a']:
                    paths.append(a['path'])
                    self.assertTrue(a['ok'], a)
        self.assertEqual(len(paths), 3)
        for p in paths:
            self.assertNotIn('\\', p)
            self.assertFalse(p.startswith('/') or re.match(r'^[A-Za-z]:', p), p)
            self.assertNotIn(' ', p)
            self.assertNotIn('#', p)  # '#' must be encoded or the browser drops the rest of the URL
            target = (self.out / unquote(p)).resolve()
            self.assertTrue(target.is_file(), p)
        raw = b''.join(f.read_bytes() for f in self.out.rglob('*.js') if f.parent.name in ('data', 'search'))
        self.assertNotIn(b'\r\n', raw)

    def test_rebuild_swaps_dirs(self):
        self.build()
        (self.out / 'data' / '99.js').write_text('stale', encoding='utf-8')
        self.build()  # must replace the existing data/ and search/ (Windows cannot rename over a dir)
        self.assertFalse((self.out / 'data' / '99.js').exists())
        self.assertEqual(len(self.chats()), 2)
        for leftover in ('data.new', 'search.new', 'data.old', 'search.old', 'index_data.js.tmp'):
            self.assertFalse((self.out / leftover).exists(), leftover)


if __name__ == '__main__':
    unittest.main()
