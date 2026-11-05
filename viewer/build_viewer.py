#!/usr/bin/env python3
"""sigexport-browser: build offline viewer data from sigexport output.
Usage: build_viewer.py [--root DIR] [--out DIR]
  --root  export folder holding chat folders (default: parent of this script's folder)
  --out   viewer folder to write index_data.js and data/ into (default: this script's folder)"""
import argparse, json, os, re, sys, unicodedata
from datetime import datetime
from urllib.parse import quote

IMG = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.avif'}
VID = {'.mp4', '.mov', '.m4v', '.webm', '.mkv', '.3gp', '.avi'}
AUD = {'.mp3', '.m4a', '.aac', '.ogg', '.oga', '.opus', '.wav', '.amr', '.flac'}


FOLD = {'æ': 'ae', 'ø': 'o', 'å': 'a', 'ß': 'ss', 'œ': 'oe', 'đ': 'd', 'ł': 'l', 'þ': 'th'}


def norm(s):
    s = ''.join(FOLD.get(c, c) for c in (s or '').lower())
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))


LINKRE = re.compile(r'https?://\S+')
TBIT = {'image': 1, 'video': 2, 'audio': 4, 'file': 8}


def ftype(p):
    e = os.path.splitext(p)[1].lower()
    return 'image' if e in IMG else 'video' if e in VID else 'audio' if e in AUD else 'file'


def load(path):
    txt = open(path, encoding='utf-8', errors='replace').read()
    try:
        d = json.loads(txt)
        return d if isinstance(d, list) else [d]
    except ValueError:
        pass
    out, dec, i = [], json.JSONDecoder(), 0
    while i < len(txt):
        while i < len(txt) and txt[i].isspace():
            i += 1
        if i >= len(txt):
            break
        try:
            o, i = dec.raw_decode(txt, i)
        except ValueError:
            break
        out.append(o)
    return out


def pdate(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def clean_quote(q):
    if not q:
        return ''
    lines = [re.sub(r'^\s*>\s?', '', l) for l in str(q).splitlines()]
    return '\n'.join(l for l in lines if l.strip()).strip()


MARKERS = ('.nodedup', '.incomplete', 'INCOMPLETE', '.incomplete.txt')


def find_chats(root, out):
    """Chat dirs (relative to root, '/'-separated) holding data.json.
    sigexport nests one level: ROOT/NNN_safe/<Chat Name>/data.json; also accept ROOT/NNN/data.json."""
    if not os.path.isdir(root):
        return []
    res = []
    for d in sorted(os.listdir(root)):
        top = os.path.join(root, d)
        if not os.path.isdir(top) or d.startswith('.') or os.path.abspath(top) == out:
            continue
        if os.path.isfile(os.path.join(top, 'data.json')):
            res.append(d)
        for sub in sorted(os.listdir(top)):
            if not sub.startswith('.') and os.path.isfile(os.path.join(top, sub, 'data.json')):
                res.append(d + '/' + sub)
    return res


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description='Build sigexport-browser data files.')
    ap.add_argument('--root', default=os.path.dirname(here), help='export folder (default: parent of viewer folder)')
    ap.add_argument('--out', default=here, help='viewer folder (default: folder of this script)')
    a = ap.parse_args()
    root, out = os.path.abspath(a.root), os.path.abspath(a.out)
    for sd in ('data', 'search'):
        os.makedirs(os.path.join(out, sd), exist_ok=True)
        for f in os.listdir(os.path.join(out, sd)):
            if f.endswith('.js'):
                os.remove(os.path.join(out, sd, f))
    sbytes = 0
    dirs = find_chats(root, out)
    index, nmsg, nmedia, unres, skipped = [], 0, 0, 0, 0
    for d in dirs:
        cd = os.path.join(root, d)
        top = os.path.join(root, d.split('/')[0])
        if any(os.path.exists(os.path.join(x, m)) for x in {cd, top} for m in MARKERS):
            skipped += 1
            continue
        try:
            raw = load(os.path.join(cd, 'data.json'))
        except Exception:
            skipped += 1
            continue
        if not any(isinstance(m, dict) and ('date' in m or 'body' in m) for m in raw):
            skipped += 1  # not a message export (e.g. sticker pack metadata) or empty
            continue
        n = len(index)
        msgs, media = [], 0
        for m in raw:
            if not isinstance(m, dict):
                continue
            dt = pdate(m.get('date') or '')
            ts = int(dt.timestamp() * 1000) if dt else 0
            atts = []
            for at in m.get('attachments') or []:
                if not isinstance(at, dict):
                    continue
                p = at.get('path') or ''
                full = os.path.normpath(os.path.join(cd, p)) if p else ''
                ok = bool(p) and os.path.isfile(full)
                rel = os.path.relpath(full, out) if p else ''
                size = os.path.getsize(full) if ok else 0
                nm = at.get('name') or os.path.basename(p)
                atts.append({'path': '/'.join(quote(s) for s in rel.split(os.sep)) if p else '',
                             'type': ftype(p or nm), 'name': nm, 'size': size, 'ok': ok})
                media += 1
                if not ok:
                    unres += 1
            sender = m.get('sender') or ''
            kind = ''
            if m.get('deleted'): kind = 'deleted'
            elif m.get('call'): kind = 'missed call' if m.get('missed') else 'call'
            msgs.append({'t': ts, 'd': m.get('date') or '', 's': sender, 'o': sender == 'Me',
                         'b': m.get('body') or '', 'q': clean_quote(m.get('quote')),
                         'r': [x for x in (m.get('reactions') or []) if isinstance(x, (list, tuple))],
                         'a': atts, 'k': kind})
        msgs.sort(key=lambda x: x['t'])
        name = d.split('/')[-1] if '/' in d else re.sub(r'^\d+_', '', d)
        senders = sorted({x['s'] for x in msgs if not x['o'] and x['s']})
        chat = {'id': n, 'dir': d, 'name': name, 'group': len(senders) > 1, 'messages': msgs}
        with open(os.path.join(out, 'data', '%d.js' % n), 'w', encoding='utf-8') as f:
            f.write('window.CHATS=window.CHATS||{};window.CHATS[%d]=' % n)
            json.dump(chat, f, ensure_ascii=False, separators=(',', ':'))
            f.write(';\n')
        # search shard: per message (same order as data/<n>.js) [t, sender idx, orig text, norm text or 0, type bits]
        sl, sx, rows = [], {}, []
        for x in msgs:
            sn = 'Me' if x['o'] else x['s']
            if sn not in sx:
                sx[sn] = len(sl); sl.append(sn)
            txt = x['b'] or ''
            names = ' '.join(a['name'] for a in x['a'] if a.get('name'))
            if names:
                txt = (txt + '\n' if txt else '') + '\U0001F4CE ' + names
            fl = 0
            for a in x['a']:
                fl |= TBIT.get(a['type'], 8)
            if LINKRE.search(x['b'] or ''):
                fl |= 16
            nt = norm(txt)
            rows.append([x['t'] // 1000, sx[sn], txt, 0 if nt == txt else nt, fl])
        sp = os.path.join(out, 'search', '%d.js' % n)
        with open(sp, 'w', encoding='utf-8') as f:
            f.write('window.SEARCH=window.SEARCH||{};window.SEARCH[%d]=' % n)
            json.dump({'s': sl, 'm': rows}, f, ensure_ascii=False, separators=(',', ':'))
            f.write(';\n')
        sbytes += os.path.getsize(sp)
        last = msgs[-1] if msgs else None
        snip = ''
        if last:
            snip = last['b'][:80] or ('[%s]' % last['a'][0]['type'] if last['a'] else last['k'])
        index.append({'id': n, 'name': name, 'count': len(msgs), 'media': media,
                      'last': last['t'] if last else 0, 'snippet': snip, 'group': chat['group'],
                      'lo': bool(last and last['o']), 'ls': (last['s'] if last else '')})
        nmsg += len(msgs)
        nmedia += media
    with open(os.path.join(out, 'index_data.js'), 'w', encoding='utf-8') as f:
        f.write('window.CHAT_INDEX=')
        json.dump(index, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')
    print('chats=%d skipped=%d messages=%d media=%d unresolved_media=%d search_index_bytes=%d' % (len(index), skipped, nmsg, nmedia, unres, sbytes))
    if not index:
        print('No chats yet - rerun this build after more chats export.')


if __name__ == '__main__':
    main()
