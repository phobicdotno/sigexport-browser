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


    def test_stamp_carries_version_and_install_copy_sets_code_version(self):
        self.build()
        self.assertIn("window.__VIEWER_VERSION=\"dev\";", (self.out / 'build_stamp.js').read_text(encoding='utf-8'))
        sys.path.insert(0, str(REPO / 'tools'))
        import install_sync
        v = install_sync.copy_viewer_files(self.out)
        self.assertEqual(v, (REPO / 'VERSION').read_text(encoding='utf-8').strip())
        self.assertEqual((self.out / 'VERSION').read_text(encoding='utf-8').strip(), v)
        self.assertIn("var CODE_VERSION='%s';" % v, (self.out / 'viewer.js').read_text(encoding='utf-8'))
        self.assertIn("var CODE_VERSION='dev';", (REPO / 'viewer' / 'viewer.js').read_text(encoding='utf-8'))
        self.build()
        stamp = (self.out / 'build_stamp.js').read_text(encoding='utf-8')
        self.assertIn('window.__VIEWER_VERSION="%s";' % v, stamp)
        self.assertRegex(stamp, r"__BUILD_TIME='\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d'")
    def test_real_chat_names_with_emoji_and_spaces(self):
        """sigexport names the chat folder 'JaneredheartDoe'; the viewer shows the real name."""
        def chat(top, sub, sender):
            d = self.root / top / sub
            d.mkdir(parents=True)
            (d / 'data.json').write_text(json.dumps({'date': '2024-01-02T03:04:05', 'sender': sender,
                                                     'body': 'hi', 'reactions': [[sender, 'x']]}) + '\n',
                                         encoding='utf-8')
        chat('301_Jane_Doe', 'JaneredheartDoe', 'JaneredheartDoe')
        chat('302_Bob', 'BobSmith', 'BobSmith')
        chat('302_Bob', 'Bob', 'Bob')
        chat('303_Other', 'NoMatch', 'NoMatch')
        st = self.root / '_state'
        st.mkdir(exist_ok=True)
        lines = [''] * 303
        lines[300] = '\u2068Jane \u2764\ufe0f Doe\u2069'
        lines[301] = 'Bob'
        lines[302] = 'Somebody Else'
        (st / 'chats.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        (st / 'folders.tsv').write_text('302\tBob Smith\tBobSmith\n', encoding='utf-8')
        self.build()
        names = {c['name'] for c in payload((self.out / 'index_data.js').read_text(encoding='utf-8'))}
        self.assertIn('Jane \u2764\ufe0f Doe', names)
        self.assertIn('Bob Smith', names)
        self.assertIn('Bob', names)
        self.assertIn('NoMatch', names)  # no safe name match -> folder name kept
        self.assertNotIn('JaneredheartDoe', names)
        jane = [c for c in self.chats().values() if c['dir'] == '301_Jane_Doe/JaneredheartDoe'][0]
        self.assertEqual(jane['messages'][0]['s'], 'Jane \u2764\ufe0f Doe')
        self.assertEqual(jane['messages'][0]['r'][0][0], 'Jane \u2764\ufe0f Doe')
        srch = ''.join(f.read_text(encoding='utf-8') for f in (self.out / 'search').glob('*.js'))
        self.assertNotIn('redheart', srch)

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


class CentralLayoutTest(unittest.TestCase):
    """Central media store: data.json paths are ../../Media/<Type>/<file>."""
    def test_central_paths_resolve(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            root = tmp / 'export'
            shutil.copytree(REPO / 'tests' / 'fixtures' / 'export_central', root)
            out = root / 'viewer'
            shutil.copytree(REPO / 'viewer', out)
            r = subprocess.run([sys.executable, '-I', str(out / 'build_viewer.py'), '--root', str(root),
                                '--out', str(out)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('chats=1', r.stdout)
            self.assertIn('unresolved_media=0', r.stdout)
            n = 0
            for f in (out / 'data').glob('*.js'):
                for m in payload(f.read_text(encoding='utf-8'))['messages']:
                    for a in m['a']:
                        n += 1
                        self.assertTrue(a['ok'], a)
                        self.assertTrue(a['path'].startswith('../Media/'), a['path'])
                        self.assertIn('%23', a['path']) if 'clip' in a['path'] else None
                        self.assertTrue((out / unquote(a['path'])).is_file(), a['path'])
            self.assertEqual(n, 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
