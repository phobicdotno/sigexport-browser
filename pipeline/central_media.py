#!/usr/bin/env python3
"""Central media store: every unique file lives once in OUT_DIR/Media/<Type>/<file>.

Per exported chat folder (NNN_x), after sigexport:
  1. hard-link backup of NNN_x into OUT_DIR/_work/backup/NNN_x (costs no space)
  2. hash every real media file (<chat>/media/* and exported_stickers/*); build a plan:
       known md5 -> point refs at the existing central file, delete the copy
       new md5   -> move into Media/<Type>/<name> (name clash -> _<md5[:8]> before the extension)
     plus, per chat file, which refs resolved before. The plan is the journal
     (STATE/central_journal/NNN_x.json, written atomically before anything moves).
  3. move new files (rename, never overwrite), append md5\tMedia/<Type>/<file> to media_index.tsv
  4. rewrite chat.md / index.html (URL-quoted, html-escaped) and data.json (plain) refs
     to ../../Media/<Type>/<file>, each file atomically
  5. delete duplicate copies, link check (every ref that resolved before still resolves,
     the number of refs is unchanged, no ref points into a chat media/ any more)
  6. rmdir empty media/ and exported_stickers/, drop journal and backup.
Every step is idempotent, so a crash at any point is finished by resume(); if that fails,
rollback() restores the backup and takes the moved files back out of Media/.
Stdlib only; prints no message text and no names.
"""
from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import quote, unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dedup as D  # noqa: E402  (md5, regexes, atomic_write, rd, chat_dirs)

TYPES = ('Pictures', 'Videos', 'Audio', 'Documents')
PIC = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif', '.bmp', '.tif', '.tiff', '.svg', '.avif'}
VID = {'.mp4', '.mov', '.m4v', '.3gp', '.webm', '.mkv', '.avi', '.mpg', '.mpeg'}
AUD = {'.m4a', '.aac', '.mp3', '.ogg', '.oga', '.opus', '.wav', '.flac', '.amr', '.weba', '.caf', '.aiff'}
RXS = (('chat.md', D.MD_RE2, 'md'), ('index.html', D.HT_RE2, 'html'), ('data.json', D.JS_RE2, 'json'))
STICKERS = 'exported_stickers'


class CentralError(Exception):
    pass


def _mime_type(path: str) -> str:
    if not shutil.which('file'):
        return ''
    try:
        r = subprocess.run(['file', '-b', '--mime-type', path], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, encoding='utf-8', timeout=30)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def classify(path: str, sticker: bool = False) -> str:
    """Pictures/Videos/Audio/Documents by extension, then mimetypes, then `file --mime-type`."""
    if sticker:
        return 'Pictures'
    ext = os.path.splitext(path)[1].lower()
    mt = mimetypes.guess_type(path)[0] or ''
    if ext in PIC or mt.startswith('image/'):
        return 'Pictures'
    if ext in VID or mt.startswith('video/'):
        return 'Videos'
    if ext in AUD or mt.startswith('audio/'):
        return 'Audio'
    if ext in ('', '.bin', '.dat', '.tmp') or not mt:
        mt = _mime_type(path)
        for pre, t in (('image/', 'Pictures'), ('video/', 'Videos'), ('audio/', 'Audio')):
            if mt.startswith(pre):
                return t
    return 'Documents'


def load_index(p: str) -> dict:
    return D.load_index(p)


def media_files(root: str, top: str) -> list[str]:
    """Real (non-symlink) media files of an NNN folder, relpaths from root, '/'-separated."""
    out = []
    b = os.path.join(root, top)
    if not os.path.isdir(b):
        return out
    for sub in sorted(os.listdir(b)):
        sp = os.path.join(b, sub)
        mdir = sp if sub == STICKERS else os.path.join(sp, 'media')
        if not os.path.isdir(mdir) or os.path.islink(mdir):
            continue
        for fn in sorted(os.listdir(mdir)):
            p = os.path.join(mdir, fn)
            if os.path.isfile(p) and not os.path.islink(p):
                out.append(D.posix_rel(p, root))
    return out


def _dec(ref, key):
    if key == 'html':
        return html.unescape(ref)
    if key == 'json':
        return json.loads('"' + ref + '"')
    return ref


def _enc(rel, key):
    if key == 'json':
        return json.dumps(rel, ensure_ascii=False)[1:-1]
    q = quote(rel, safe="/._-~")
    return html.escape(q, quote=True) if key == 'html' else q


_WS = re.compile(r'\s')


def ws_same(disk_name, ref_name):
    """True if ref_name is how sigexport refers to the file disk_name. sigexport keeps U+00A0 (and
    other non-ASCII whitespace) in the file name but writes re.sub(r"\\s", "%20", path) into its
    refs, and %20 decodes to a plain space, so neither the raw nor the URL-decoded ref names the file."""
    return _WS.sub('%20', disk_name) == ref_name or \
        _WS.sub(' ', disk_name) == _WS.sub(' ', unquote(ref_name))


def ws_variant(names, ref_name):
    """The one name in names that ref_name refers to modulo whitespace (see ws_same), else None.
    Only looked for when the decoded ref has whitespace; never guesses between several."""
    if not _WS.search(unquote(ref_name)):
        return None
    hits = sorted({n for n in names if ws_same(n, ref_name)})
    return hits[0] if len(hits) == 1 else None


def _targets(root, cd, ref, known=()):
    """Candidate relpaths (from root, '/'-separated) a ref points to: raw, then URL-decoded, then
    (when none of those is a file) the file that differs only in whitespace (ws_variant), looked
    for on disk and among known relpaths (the journal's sources, already moved by rewrite time)."""
    if not ref or ref.startswith('#') or re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', ref):
        return []
    r = ref.replace('\\', '/')
    out = []
    for cand in (r, unquote(r), r.split('#')[0]):
        a = os.path.normpath(os.path.join(cd, *cand.split('/')))
        rel = D.posix_rel(a, root)
        if rel not in out:
            out.append(rel)
    d, _, base = r.split('#')[0].rpartition('/')
    if _WS.search(unquote(base)) and not _resolves(root, out):
        adir = os.path.normpath(os.path.join(cd, *d.split('/'))) if d else cd
        drel = D.posix_rel(adir, root)
        names = [k.rpartition('/')[2] for k in known if k.rpartition('/')[0] == drel]
        if os.path.isdir(adir):
            names += [f for f in os.listdir(adir) if os.path.isfile(os.path.join(adir, f))]
        hit = ws_variant(names, base)
        if hit:
            out.append(drel + '/' + hit)
    return out


def _resolves(root, rels):
    return any(os.path.isfile(os.path.join(root, *x.split('/'))) for x in rels)


def ref_state(root: str, cd: str) -> dict:
    """{fname: [resolved?, ...]} for every ref in the chat's three files (order preserved)."""
    out = {}
    for fname, rx, key in RXS:
        fp = os.path.join(cd, fname)
        if os.path.exists(fp):
            out[fname] = [_resolves(root, _targets(root, cd, _dec(mo.group(1), key)))
                          for mo in rx.finditer(D.rd(fp))]
    return out


def rewrite_chat(root: str, cd: str, mapping: dict) -> int:
    """Point every ref whose target is in mapping (old rel -> central rel) at the central file."""
    n = 0
    for fname, rx, key in RXS:
        fp = os.path.join(cd, fname)
        if not os.path.exists(fp):
            continue
        t = D.rd(fp)

        def cb(mo):
            nonlocal n
            hit = next((x for x in _targets(root, cd, _dec(mo.group(1), key), mapping) if x in mapping),
                       None)
            if not hit:
                return mo.group(0)
            n += 1
            new = _enc(D.posix_rel(os.path.join(root, *mapping[hit].split('/')), cd), key)
            s, e = mo.start(1) - mo.start(0), mo.end(1) - mo.start(0)
            return mo.group(0)[:s] + new + mo.group(0)[e:]
        t2 = rx.sub(cb, t)
        if t2 != t:
            D.atomic_write(fp, t2)
    return n


def _atomic_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    t = p + '.tmpw'
    with open(t, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(t, p)


def _append_index(index_p, lines):
    if not lines:
        return
    with open(index_p, 'a', encoding='utf-8', newline='\n') as f:
        for m, c in lines:
            f.write(f'{m}\t{c}\n')
        f.flush()
        os.fsync(f.fileno())


class Central:
    def __init__(self, root: str, state: str, work: str | None = None):
        self.root = os.path.abspath(root)
        self.state = os.path.abspath(state)
        self.work = os.path.abspath(work or os.path.join(self.root, '_work'))
        self.index_p = os.path.join(self.state, 'media_index.tsv')
        self.jdir = os.path.join(self.state, 'central_journal')
        self.bdir = os.path.join(self.work, 'backup')

    def journal_p(self, top):
        return os.path.join(self.jdir, top + '.json')

    def backup_p(self, top):
        return os.path.join(self.bdir, top)

    def pending(self) -> list[str]:
        if not os.path.isdir(self.jdir):
            return []
        return sorted(f[:-5] for f in os.listdir(self.jdir) if f.endswith('.json'))

    # -- step 1+2
    def backup(self, top):
        b = self.backup_p(top)
        if os.path.exists(b):
            shutil.rmtree(b)
        os.makedirs(self.bdir, exist_ok=True)
        shutil.copytree(os.path.join(self.root, top), b, copy_function=os.link, symlinks=True)

    def plan(self, top) -> dict:
        index = load_index(self.index_p)
        used = set()
        for t in TYPES:
            d = os.path.join(self.root, 'Media', t)
            if os.path.isdir(d):
                used.update((t, fn) for fn in os.listdir(d))
        entries, new_by_md5 = [], {}
        for rel in media_files(self.root, top):
            ap = os.path.join(self.root, *rel.split('/'))
            m = D.md5(ap)
            size = os.path.getsize(ap)
            known = index.get(m) or new_by_md5.get(m)
            if known and (known in new_by_md5.values() or os.path.isfile(os.path.join(self.root, *known.split('/')))):
                entries.append({'src': rel, 'dst': known, 'md5': m, 'size': size, 'op': 'dup'})
                continue
            t = classify(ap, sticker=rel.split('/')[1] == STICKERS)
            fn = rel.rsplit('/', 1)[1]
            if (t, fn) in used:
                b, e = os.path.splitext(fn)
                fn = f'{b}_{m[:8]}{e}'
                k = 12
                while (t, fn) in used:
                    fn = f'{b}_{m[:k]}{e}'
                    k += 4
            used.add((t, fn))
            dst = f'Media/{t}/{fn}'
            new_by_md5[m] = dst
            entries.append({'src': rel, 'dst': dst, 'md5': m, 'size': size, 'op': 'move'})
        before = {D.posix_rel(cd, self.root): ref_state(self.root, cd)
                  for cd in D.chat_dirs(os.path.join(self.root, top))}
        return {'top': top, 'entries': entries, 'before': before}

    # -- steps 3-6, idempotent
    def apply(self, j) -> dict:
        root, top = self.root, j['top']
        index = load_index(self.index_p)
        new_lines = []
        for e in j['entries']:
            if e['op'] != 'move':
                continue
            s = os.path.join(root, *e['src'].split('/'))
            d = os.path.join(root, *e['dst'].split('/'))
            if os.path.exists(s) and not os.path.exists(d):
                os.makedirs(os.path.dirname(d), exist_ok=True)
                os.rename(s, d)
            elif os.path.exists(s) and os.path.exists(d):
                if os.path.getsize(d) != e['size'] or D.md5(d) != e['md5']:
                    raise CentralError('central name taken by another file')
            elif not os.path.exists(d):
                raise CentralError('planned file vanished')
            if e['md5'] not in index:
                index[e['md5']] = e['dst']
                new_lines.append((e['md5'], e['dst']))
        _append_index(self.index_p, new_lines)
        mapping = {e['src']: e['dst'] for e in j['entries']}
        cds = D.chat_dirs(os.path.join(root, top))
        n = sum(rewrite_chat(root, cd, mapping) for cd in cds)
        saved = 0
        for e in j['entries']:
            s = os.path.join(root, *e['src'].split('/'))
            if e['op'] == 'dup' and os.path.isfile(s):
                saved += e['size']
                os.remove(s)
            elif e['op'] == 'move' and os.path.isfile(s):
                os.remove(s)  # identical copy left by a crash between rename checks
        self.linkcheck(j, mapping)
        for sub in os.listdir(os.path.join(root, top)):
            sp = os.path.join(root, top, sub)
            for d in ((sp,) if sub == STICKERS else (os.path.join(sp, 'media'),)):
                if os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
        return {'media': len(j['entries']), 'deduped': sum(1 for e in j['entries'] if e['op'] == 'dup'),
                'moved': sum(1 for e in j['entries'] if e['op'] == 'move'), 'saved': saved,
                'kept': sum(e['size'] for e in j['entries'] if e['op'] == 'move'), 'refs_rewritten': n}

    def linkcheck(self, j, mapping=None) -> dict:
        mapping = mapping or {e['src']: e['dst'] for e in j['entries']}
        total = broke = ignored = local = 0
        for crel, before in j['before'].items():
            cd = os.path.join(self.root, *crel.split('/'))
            after = ref_state(self.root, cd)
            for fname, b in before.items():
                a = after.get(fname, [])
                if len(a) != len(b):
                    raise CentralError(f'link check: ref count changed in {fname}')
                total += len(b)
                ignored += b.count(False)  # did not resolve before (sigexport-reported missing)
                broke += sum(1 for x, y in zip(b, a) if x and not y)
            for fname, rx, key in RXS:
                fp = os.path.join(cd, fname)
                if os.path.exists(fp):
                    for mo in rx.finditer(D.rd(fp)):
                        if any(x in mapping for x in _targets(self.root, cd, _dec(mo.group(1), key), mapping)):
                            local += 1
        if broke or local:
            raise CentralError(f'link check: broken={broke} still_local={local}')
        return {'links': total, 'missing_reported': ignored}

    def finish(self, top):
        jp = self.journal_p(top)
        if os.path.exists(jp):
            os.remove(jp)
        shutil.rmtree(self.backup_p(top), ignore_errors=True)

    def rollback(self, top):
        """Restore NNN_x from the hard-link backup and take this chat's new files out of Media/."""
        jp = self.journal_p(top)
        j = json.load(open(jp, encoding='utf-8')) if os.path.exists(jp) else {'entries': []}
        b = self.backup_p(top)
        if not os.path.isdir(b):
            raise CentralError('no backup to restore')
        moved = {e['md5']: e['dst'] for e in j['entries'] if e['op'] == 'move'}
        for m, dst in moved.items():
            d = os.path.join(self.root, *dst.split('/'))
            if os.path.isfile(d):
                os.remove(d)  # the backup still holds a hard link to the same data
        if moved and os.path.exists(self.index_p):
            keep = [ln for ln in D.rd(self.index_p).splitlines(True)
                    if not (ln.split('\t', 1)[0] in moved and ln.rstrip('\n').split('\t', 1)[-1] == moved[ln.split('\t', 1)[0]])]
            D.atomic_write(self.index_p, ''.join(keep))
        w = os.path.join(self.root, top)
        if os.path.exists(w):
            shutil.rmtree(w)
        os.rename(b, w)
        if os.path.exists(jp):
            os.remove(jp)

    def run(self, top) -> dict:
        """Centralise one freshly exported NNN folder. Raises CentralError after rolling back."""
        self.backup(top)
        j = self.plan(top)
        _atomic_json(self.journal_p(top), j)
        return self.resume(top)

    def resume(self, top) -> dict:
        with open(self.journal_p(top), encoding='utf-8') as f:
            j = json.load(f)
        try:
            res = self.apply(j)
        except (CentralError, OSError) as ex:
            self.rollback(top)
            raise CentralError(f'{ex} -> restored from backup') from ex
        self.finish(top)
        return res


def verify_order(root: str, names: list[str]) -> list[str]:
    """Port of verify_chats.py: every existing NNN_x folder (also in _dupe_chats/_incomplete)
    must equal chat_dir_name(NNN, names[NNN-1]). Returns mismatching NNNs."""
    from platform_util import safe_component
    ds = []
    for sub in ('', '_dupe_chats', '_incomplete'):
        b = os.path.join(root, sub)
        if os.path.isdir(b):
            ds += [d for d in os.listdir(b) if re.match(r'^\d{3}_', d) and os.path.isdir(os.path.join(b, d))]
    bad = []
    for d in sorted(ds):
        i = int(d[:3])
        if i < 1 or i > len(names) or f'{i:03d}_{safe_component(names[i - 1])}' != d:
            bad.append(d[:3])
    return bad
