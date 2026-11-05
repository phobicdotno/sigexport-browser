#!/usr/bin/env python3
"""dedup.py dedup|linkcheck|indexonly ROOT IDXDIR INDEX [--dry-run]
IDXDIR = top-level dir name of the chat under ROOT (e.g. 006_Name). Prints one JSON line. No message text is printed.

Attachments sigexport itself could not export are not link failures:
- data.json attachments with "missing_kind" set (sigexport placeholder, empty path), and
- data.json/chat.md refs to ./media/<file> where <file> never existed in the chat's
  media/ dir before dedup (sigexport's "No file to copy ... skipping!" case).
dedup/indexonly record the pre-dedup media listing in <dir of INDEX>/present/<IDXDIR>.json
so linkcheck can tell "never exported" from "broken by dedup". Those are reported as
missing_reported and do not fail the link check. Optional env LC_SIGLOG=<sigexport output>
adds a no_file_lines count (corroboration only)."""
import sys, os, re, json, hashlib, html

def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()

def chat_dirs(base):
    return sorted(os.path.join(base, d) for d in os.listdir(base)
                  if os.path.isfile(os.path.join(base, d, 'chat.md')))

def load_index(p):
    idx = {}
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.rstrip('\n')
            if '\t' in line:
                m, path = line.split('\t', 1)
                idx.setdefault(m, path)
    return idx

# sigexport builds attachment paths with pathlib (create.py: Path("media") / file_name), so on
# Windows they come out as ./media\file (JSON: media\\file). Accept both separators and always
# write forward slashes back.
MD_RE = re.compile(r'(\]\()\./media[/\\]([^\n]*?)(\))(?=[ \t]*(?:\n|$|!?\[))')
HT_RE = re.compile(r'((?:src|href)=")\./media[/\\]([^"]*)(")')
JS_RE = re.compile(r'("path": ")media(?:/|\\\\)((?:[^"\\]|\\.)*)(")')
PREFIX = {'md': './media/', 'html': './media/', 'json': 'media/'}

def posix_rel(path, start):
    """Relative path with forward slashes on every OS (links never use os.sep)."""
    return os.path.relpath(path, start).replace(os.sep, '/')

def atomic_write(p, s):
    t = p + '.tmpw'
    with open(t, 'w', encoding='utf-8', newline='') as f:
        f.write(s)
    os.replace(t, p)

def rd(p):
    with open(p, encoding='utf-8', newline='') as f:
        return f.read()

def main():
    mode, root, idxdir, indexp = sys.argv[1:5]
    dry = '--dry-run' in sys.argv
    base = os.path.join(root, idxdir)
    if mode == 'linkcheck':
        return linkcheck(root, base, indexp, idxdir)
    index = load_index(indexp)
    record_present(base, root, indexp, idxdir, dry)
    stats = dict(media=0, deduped=0, saved=0, kept=0, repl_md=0, repl_html=0, repl_json=0, errors=[])
    newentries = []
    verified = {}
    for cd in chat_dirs(base):
        rel = posix_rel(cd, root)
        mdir = os.path.join(cd, 'media')
        mapping = {}  # file name -> new relative ref (from cd)
        files = sorted(os.listdir(mdir)) if os.path.isdir(mdir) else []
        for fn in files:
            p = os.path.join(mdir, fn)
            if not os.path.isfile(p) or os.path.islink(p):
                continue
            size = os.path.getsize(p)
            stats['media'] += 1
            if size == 0:
                stats['kept'] += 0
                continue
            m = md5(p)
            first = index.get(m)
            if first is None:
                index[m] = rel + '/media/' + fn
                newentries.append((m, index[m]))
                stats['kept'] += size
                continue
            fp = os.path.join(root, first)
            if os.path.abspath(fp) == os.path.abspath(p):
                stats['kept'] += size
                continue
            ok = verified.get(fp)
            if ok is None:
                ok = os.path.isfile(fp) and md5(fp) == m
                verified[fp] = ok
            if not ok:
                stats['errors'].append('first copy missing/mismatch for index entry')
                stats['kept'] += size
                continue
            newrel = posix_rel(fp, cd)
            if not newrel.startswith('.'):
                newrel = './' + newrel
            mapping[fn] = (newrel, p, size)
        if mode == 'indexonly':
            mapping.clear()
        # rewrite text files
        def make_cb(counter):
            def cb(mo):
                name = mo.group(2)
                if counter == 'html':
                    name = html.unescape(name)
                elif counter == 'json':
                    name = json.loads('"' + name + '"')
                ent = mapping.get(name)
                if not ent:
                    # keep the name exactly as written, only normalise the separator
                    return mo.group(1) + PREFIX[counter] + mo.group(2) + mo.group(3)
                stats['repl_' + counter] += 1
                nr = ent[0]
                if counter == 'html':
                    nr = html.escape(nr, quote=True)
                elif counter == 'json':
                    nr = json.dumps(nr, ensure_ascii=False)[1:-1]
                    nr = nr  # path relative incl. ./ or ../
                    return mo.group(1) + nr + mo.group(3)
                return mo.group(1) + nr + mo.group(3)
            return cb
        if True:  # always run: also normalises Windows separators to '/'
            for fname, rx, key in (('chat.md', MD_RE, 'md'), ('index.html', HT_RE, 'html'), ('data.json', JS_RE, 'json')):
                fp = os.path.join(cd, fname)
                if not os.path.exists(fp):
                    continue
                t = rd(fp)
                if key == 'json':
                    # json paths: keep without leading ./ when same chat; ../ otherwise
                    def cbj(mo, _cb=make_cb('json')):
                        return _cb(mo)
                    t2 = rx.sub(cbj, t)
                else:
                    t2 = rx.sub(make_cb(key), t)
                if not dry and t2 != t:
                    atomic_write(fp, t2)
            # now delete duplicates (only after rewrite)
            for fn, (nr, p, size) in mapping.items():
                stats['deduped'] += 1
                stats['saved'] += size
                if not dry:
                    os.remove(p)
    if not dry and mode == 'dedup' or mode == 'indexonly' and not dry:
        pass
    stats['newentries'] = len(newentries)
    if not dry:
        with open(indexp, 'a', encoding='utf-8') as f:
            for m, p in newentries:
                f.write(m + '\t' + p + '\n')
    print(json.dumps(stats))

def present_path(indexp, idxdir):
    return os.path.join(os.path.dirname(os.path.abspath(indexp)), 'present', idxdir + '.json')

def record_present(base, root, indexp, idxdir, dry):
    """Remember which media files sigexport actually wrote (before any dedup deletes)."""
    pp = present_path(indexp, idxdir)
    if dry or os.path.exists(pp):
        return  # keep the first (pre-dedup) listing on re-runs
    pres = {}
    for cd in chat_dirs(base):
        mdir = os.path.join(cd, 'media')
        pres[posix_rel(cd, root)] = sorted(os.listdir(mdir)) if os.path.isdir(mdir) else []
    os.makedirs(os.path.dirname(pp), exist_ok=True)
    atomic_write(pp, json.dumps(pres, ensure_ascii=False))

def reported_missing_kinds(cd):
    """Count data.json attachments sigexport flagged with missing_kind."""
    fp = os.path.join(cd, 'data.json'); n = 0
    if not os.path.exists(fp):
        return 0
    for line in rd(fp).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        for a in (o.get('attachments') or []) if isinstance(o, dict) else []:
            if isinstance(a, dict) and a.get('missing_kind'):
                n += 1
    return n

def linkcheck(root, base, indexp=None, idxdir=None):
    missing = 0; total = 0; reported = set(); kinds = 0
    pres = None
    if indexp and idxdir and os.path.exists(present_path(indexp, idxdir)):
        pres = json.loads(rd(present_path(indexp, idxdir)))
    for cd in chat_dirs(base):
        kinds += reported_missing_kinds(cd)
        rel = posix_rel(cd, root)
        had = set(pres.get(rel, [])) if pres is not None else None
        for fname, rx, key in (('chat.md', MD_RE2, 'md'), ('index.html', HT_RE2, 'html'), ('data.json', JS_RE2, 'json')):
            fp = os.path.join(cd, fname)
            if not os.path.exists(fp):
                continue
            t = rd(fp)
            for mo in rx.finditer(t):
                ref = mo.group(1)
                if key == 'html':
                    ref = html.unescape(ref)
                elif key == 'json':
                    ref = json.loads('"' + ref + '"')
                ref = ref.replace('\\', '/')
                if not re.search(r'(^|/)media/', ref):
                    continue
                total += 1
                if not os.path.isfile(os.path.normpath(os.path.join(cd, ref))):
                    own = re.match(r'^(?:\./)?media/(.+)$', ref)
                    if own and had is not None and own.group(1) not in had:
                        # sigexport never wrote this file: reported missing, not a dedup break
                        reported.add((rel, own.group(1)))
                        continue
                    missing += 1
                    if os.environ.get('LC_DEBUG'): print(fname, repr(ref[:70]), file=sys.stderr)
    out = dict(links=total, missing=missing, missing_reported=len(reported) + kinds,
               missing_files=len(reported), missing_kind=kinds)
    lg = os.environ.get('LC_SIGLOG')
    if lg and os.path.exists(lg):
        out['no_file_lines'] = sum(1 for l in open(lg, encoding='utf-8', errors='replace') if 'No file to copy' in l)
    print(json.dumps(out))
    return 0 if missing == 0 else 1

MD_RE2 = re.compile(r'\]\((\.{1,2}[/\\][^\n]*?)\)(?=[ \t]*(?:\n|$|!?\[))')
HT_RE2 = re.compile(r'(?:src|href)="([^"]*)"')
JS_RE2 = re.compile(r'"path": "((?:[^"\\]|\\.)*)"')

if __name__ == '__main__':
    rc = main()
    sys.exit(rc or 0)
