#!/usr/bin/env python3
"""dedup.py dedup|linkcheck|indexonly ROOT IDXDIR INDEX [--dry-run]
IDXDIR = top-level dir name of the chat under ROOT (e.g. 006_Name). Prints one JSON line. No message text is printed."""
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
        for line in open(p, encoding='utf8'):
            line = line.rstrip('\n')
            if '\t' in line:
                m, path = line.split('\t', 1)
                idx.setdefault(m, path)
    return idx

MD_RE = re.compile(r'(\]\()\./media/([^\n]*?)(\))(?=[ \t]*(?:\n|$|!?\[))')
HT_RE = re.compile(r'((?:src|href)=")\./media/([^"]*)(")')
JS_RE = re.compile(r'("path": ")media/((?:[^"\\]|\\.)*)(")')

def atomic_write(p, s):
    t = p + '.tmpw'
    with open(t, 'w', encoding='utf8', newline='') as f:
        f.write(s)
    os.replace(t, p)

def rd(p):
    with open(p, encoding='utf8', newline='') as f:
        return f.read()

def main():
    mode, root, idxdir, indexp = sys.argv[1:5]
    dry = '--dry-run' in sys.argv
    base = os.path.join(root, idxdir)
    if mode == 'linkcheck':
        return linkcheck(root, base)
    index = load_index(indexp)
    stats = dict(media=0, deduped=0, saved=0, kept=0, repl_md=0, repl_html=0, repl_json=0, errors=[])
    newentries = []
    verified = {}
    for cd in chat_dirs(base):
        rel = os.path.relpath(cd, root)
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
            newrel = os.path.relpath(fp, cd)
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
                    return mo.group(0)
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
        if mapping:
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
        with open(indexp, 'a', encoding='utf8') as f:
            for m, p in newentries:
                f.write(m + '\t' + p + '\n')
    print(json.dumps(stats))

def linkcheck(root, base):
    missing = 0; total = 0; examined = 0
    for cd in chat_dirs(base):
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
                if not re.search(r'(^|/)media/', ref):
                    continue
                total += 1
                if not os.path.isfile(os.path.normpath(os.path.join(cd, ref))):
                    missing += 1
                    if os.environ.get('LC_DEBUG'): print(fname, repr(ref[:70]), file=sys.stderr)
    print(json.dumps(dict(links=total, missing=missing)))
    return 0 if missing == 0 else 1

MD_RE2 = re.compile(r'\]\((\.{1,2}/[^\n]*?)\)(?=[ \t]*(?:\n|$|!?\[))')
HT_RE2 = re.compile(r'(?:src|href)="([^"]*)"')
JS_RE2 = re.compile(r'"path": "((?:[^"\\]|\\.)*)"')

if __name__ == '__main__':
    rc = main()
    sys.exit(rc or 0)
