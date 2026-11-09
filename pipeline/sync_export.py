#!/usr/bin/env python3
"""Run sigexport 3.9.x in-process (run with sigexport's own venv python) for sync.py.

  sync_export.py precheck SRC WM DEST
                                   cheap no-sigexport check: one aggregate query vs the watermarks
                                   WM plus the deferred chats' pending messages; writes
                                   DEST/_fingerprints.tsv and DEST/_selected.tsv (empty = no changes)
  sync_export.py export SRC DEST [OUT] [--select FILE] [--full]
                                   export into DEST, plus DEST/_cids.tsv (conversation id <TAB>
                                   sigexport folder name), DEST/_fingerprints.tsv (cid, count,
                                   max received_at, max rowid per conversation, read from the same
                                   DB cursor) and DEST/_unavail.tsv (recent messages whose
                                   attachment file is not there yet). With --select only the cids
                                   in FILE are exported, filtered in fetch_data's SQL;
                                   DEST/_selected.tsv lists them (or "*" for all)
  sync_export.py conv-map SRC OUT  build/complete OUT/_state/conv_map.tsv only (no export)
  sync_export.py setup-key SRC     one-time, interactive: decrypt the SQLCipher key via
                                   sigexport (Keychain "Signal Safe Storage" prompt) and store
                                   it in the login Keychain as generic password
                                   service=sigexport-browser, account=$USER (ACL: /usr/bin/security)

The key is passed to sigexport in-process (never on a command line, never in a file, never
printed). If the sigexport-browser item is missing, sigexport asks the Keychain itself.
"""
import getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SERVICE = "sigexport-browser"


def stored_key():
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", SERVICE,
                            "-a", getpass.getuser(), "-w"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    k = r.stdout.strip()
    return k if r.returncode == 0 and k else None


def setup_key(src):
    import sigexport.crypto as crypto
    key = crypto.get_key(Path(src), None)
    if not key:
        raise SystemExit("could not decrypt the Signal key")
    cmd = (f'add-generic-password -U -s {SERVICE} -a {getpass.getuser()} '
           f'-l "sigexport-browser DB key" -T /usr/bin/security -w {key}\n')
    # via `security -i` stdin so the key never appears in a process list
    r = subprocess.run(["/usr/bin/security", "-i"], input=cmd, capture_output=True, text=True)
    if r.returncode != 0 or stored_key() != key:
        raise SystemExit("storing the key in the login Keychain failed")
    print("key stored in login Keychain item 'sigexport-browser'")


def _dates(path):
    from collections import Counter
    c = Counter()
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                c[json.loads(line).get("date")] += 1
    return c


def update_conv_map(out, folder2cid, convos):
    """Complete OUT/_state/conv_map.tsv (cid, NNN folder, chat subfolder) for every existing
    NNN_*/<sub>/data.json, same rules as _state/completeness_check.py: existing rows kept,
    then id_map.tsv (NNN, sub, cid), phone-only folders (NNN_phone_*) -> 'phone:<folder>',
    sigexport fix_names folder name, exact date multiset, best date overlap >= 90% (unique).
    Prints counts only. Returns number of unmapped subfolders."""
    from collections import Counter
    import sigexport.utils as utils
    out = Path(out)
    st = out / "_state"
    p = st / "conv_map.tsv"
    rows, have = [], set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            q = line.split("\t")
            if len(q) == 3:
                rows.append(q)
                have.add((q[1], q[2]))
    idmap = {}
    if (st / "id_map.tsv").exists():
        for line in (st / "id_map.tsv").read_text(encoding="utf-8").splitlines():
            q = [x.strip() for x in line.split("\t")]
            if len(q) == 3:
                idmap[(q[0], q[1])] = q[2]
    exp_dates = None
    stats = Counter()
    for dj in sorted(out.glob("[0-9][0-9][0-9]_*/*/data.json")):
        top, sub = dj.parent.parent.name, dj.parent.name
        if (top, sub) in have:
            stats["kept"] += 1
            continue
        cid = idmap.get((top[:3], sub))
        how = "id_map"
        if cid is None and re.match(r"^\d{3}_phone_", top):
            cid, how = "phone:" + top, "phone"
        if cid is None and sub in folder2cid:
            cid, how = folder2cid[sub], "name"
        if cid is None and convos is not None:
            if exp_dates is None:
                exp_dates = {k: Counter(utils.dt_from_ts(m.get_ts()).isoformat() for m in v)
                             for k, v in convos.items()}
            d = _dates(dj)
            exact = [k for k, e in exp_dates.items() if e == d]
            if len(exact) == 1:
                cid, how = exact[0], "dates"
            elif d:
                n = sum(d.values())
                scores = sorted(((sum((d & e).values()), k) for k, e in exp_dates.items()), reverse=True)
                if scores and scores[0][0] >= 0.9 * n and (len(scores) == 1 or scores[1][0] < scores[0][0]):
                    cid, how = scores[0][1], "overlap"
        if cid is None:
            stats["unmapped"] += 1
            print(f"conv_map: unmapped {top[:3]}", flush=True)
            continue
        stats[how] += 1
        rows.append([cid, top, sub])
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, p)
    print("conv_map: " + " ".join(f"{k}={v}" for k, v in sorted(stats.items())), flush=True)
    return stats["unmapped"]


def conv_map_only(src, out):
    import sigexport.data as data
    import sigexport.utils as utils
    key = stored_key()
    cur = data.get_signal_database(Path(src), password=None, key=key)
    convos, contacts, owner = data.fetch_data(Path(src), cur, chats="", include_empty=True,
                                              include_disappearing=False)
    if owner is not None:
        owner.name = "Note to Self"
        owner.is_owner = True
    contacts = utils.fix_names(contacts)
    update_conv_map(out, {c.name: k for k, c in contacts.items() if c.name}, convos)


FP_SQL = ("SELECT conversationId, COUNT(*), COALESCE(MAX(received_at), 0), MAX(rowid) "
          "FROM messages WHERE conversationId IS NOT NULL GROUP BY conversationId")


def fingerprints(cur) -> dict:
    """{cid: 'count:max_received_at:max_rowid'}: cheap per-conversation change fingerprint.
    New messages raise count/rowid; deletions lower count. In-place edits and reactions do
    not change it (merge-only would not apply them anyway; the weekly full export runs)."""
    cur.execute(FP_SQL)
    return {r[0]: f"{r[1]}:{r[2]}:{r[3]}" for r in cur.fetchall()}


DEFER_DAYS = 30           # keep equal to sync.DEFER_DAYS
AGE_OUT_MARGIN = 86400    # re-check a deferred message only once it is safely past DEFER_DAYS


def read_watermarks(p) -> dict:
    """_state/sync_watermarks.tsv: cid <TAB> fingerprint <TAB> deferred [<TAB> pending]
    (lines starting # = meta). pending = comma-separated msgid@sent_at_ms of the recent messages
    whose attachment was not available when the chat was last exported (None = column absent)."""
    wm = {}
    p = Path(p)
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            q = line.split("\t")
            if len(q) in (3, 4) and not q[0].startswith("#"):
                wm[q[0]] = (q[1], int(q[2] or 0), parse_pending(q[3]) if len(q) == 4 else None)
    return wm


def parse_pending(s: str) -> list:
    out = []
    for item in (s or "").split(","):
        mid, _, t = item.rpartition("@")
        if mid:
            try:
                out.append((mid, int(t)))
            except ValueError:
                pass
    return out


def format_pending(items) -> str:
    return ",".join(f"{m}@{int(t)}" for m, t in items)


ATT_SQL = ("SELECT path FROM message_attachments WHERE messageId = ? AND editHistoryIndex = -1 "
           "AND attachmentType = 'attachment'")  # primary-key lookup; a JOIN made SQLite scan the table


def _has_att_table(cur) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_attachments'")
    return cur.fetchone() is not None


def _att_paths(cur, mid):
    cur.execute(ATT_SQL, (mid,))
    return [r[0] for r in cur.fetchall()]


def _present(root, path) -> bool:
    return bool(path) and (root / str(path).replace("\\", "/")).is_file()


def unavailable(cur, src, since_ms: int, cids=None) -> dict:
    """{cid: [(msgid, sent_at)]}: messages sent since since_ms with an attachment whose file is not
    in SRC/attachments.noindex yet (no path, or the path's file does not exist): the candidates for
    what the merge defers (sync.py keeps only the ones matching a deferred message)."""
    if not _has_att_table(cur):
        return {}
    root = Path(src) / "attachments.noindex"
    cur.execute("SELECT conversationId, id, sent_at FROM messages WHERE sent_at >= ? AND hasAttachments = 1",
                (int(since_ms),))
    res = {}
    for cid, mid, sent in cur.fetchall():
        if cids is not None and cid not in cids:
            continue
        paths = _att_paths(cur, mid)
        if paths and not all(_present(root, p) for p in paths):
            res.setdefault(cid, []).append((mid, sent))
    return res


def pending_ready(cur, src, pending, now_ms: int) -> bool:
    """True if a deferred chat must be re-exported: one of its pending messages now has all its
    attachment files, was deleted, or is old enough to be added with missing_kind."""
    if pending is None:          # watermark from before v0.1.50: no list, re-export as before
        return True
    if not pending:              # deferred for a reason the DB cannot show; the weekly full export retries
        return False
    if not _has_att_table(cur):
        return True
    root = Path(src) / "attachments.noindex"
    for mid, sent in pending:
        if now_ms - sent >= (DEFER_DAYS * 86400 + AGE_OUT_MARGIN) * 1000:
            return True
        paths = _att_paths(cur, mid)
        if not paths or all(_present(root, p) for p in paths):
            return True
    return False


def select_cids(fp: dict, wm: dict, ready=None) -> set:
    """Conversations to export: new (no watermark), changed fingerprint, or deferred > 0 with a
    pending message that became resolvable (ready(cid, pending) -> bool; None = always)."""
    sel = set()
    for c, f in fp.items():
        w = wm.get(c)
        if w is None or w[0] != f:
            sel.add(c)
        elif w[1] > 0 and (ready is None or ready(c, w[2] if len(w) > 2 else None)):
            sel.add(c)
    return sel


def open_db(src):
    import sigexport.data as data
    return data.get_signal_database(Path(src), password=None, key=stored_key())


def precheck(src, wm_path, dest) -> int:
    """No sigexport parsing: one aggregate query + the pending checks. Writes DEST/_fingerprints.tsv
    and DEST/_selected.tsv (selected cids, empty file = nothing to export). Prints counts only."""
    import time
    cur = open_db(src)
    fp = fingerprints(cur)
    wm = read_watermarks(wm_path)
    now_ms = int(time.time() * 1000)
    ready_n = [0]

    def ready(cid, pending):
        r = pending_ready(cur, src, pending, now_ms)
        ready_n[0] += int(r)
        return r

    sel = select_cids(fp, wm, ready)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with open(dest / "_fingerprints.tsv", "w", encoding="utf-8") as f:
        f.write("".join(f"{c}\t{v}\n" for c, v in fp.items()))
    with open(dest / "_selected.tsv", "w", encoding="utf-8") as f:
        f.write("".join(f"{c}\n" for c in sorted(sel)))
    deferred = sum(1 for w in wm.values() if w[1] > 0)
    print(f"precheck: convos={len(fp)} selected={len(sel)} deferred_chats={deferred} "
          f"deferred_ready={ready_n[0]}", flush=True)
    return 0


def read_selected(p):
    lines = [x.strip() for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]
    return None if lines == ["*"] else set(lines)


class CidFilterCursor:
    """Cursor proxy for sigexport's fetch_data: the messages query gets
    'WHERE conversationId IN (...)' so only the selected chats are read and JSON-parsed."""

    def __init__(self, cur, cids):
        self._c, self._cids = cur, sorted(cids)

    def execute(self, sql, *a):
        if "FROM messages" in sql and "ORDER BY sent_at" in sql and "conversationId," in sql and self._cids:
            qs = ",".join("?" * len(self._cids))
            head, tail = sql.rsplit("ORDER BY sent_at", 1)
            cond = f"conversationId IN ({qs})"
            head = head.replace("WHERE ", f"WHERE {cond} AND ", 1) if "WHERE " in head \
                else head.rstrip() + f"\n    WHERE {cond}\n    "
            self._c.execute(head + "ORDER BY sent_at" + tail, tuple(self._cids))
        else:
            self._c.execute(sql, *a)
        return self

    def __iter__(self):
        return iter(self._c)

    def __getattr__(self, k):
        return getattr(self._c, k)


class NothingToExport(Exception):
    pass


def export(src, dest, out=None, select_path=None, full=True):
    """Full export, or (select_path, not full) only the cids listed in select_path (written by
    precheck). The selection is applied in fetch_data's SQL, so other chats are never parsed."""
    import time
    import sigexport.data as data
    import sigexport.main as smain
    import sigexport.utils as utils
    mapping, captured = {}, {}
    _fix, _fetch = utils.fix_names, data.fetch_data
    sel = None if (full or not select_path) else read_selected(select_path)
    if sel is not None and not sel:
        captured["selected"] = set()

    def fetch_record(*a, **k):
        a = list(a)
        cur = a[1] if len(a) > 1 else k.get("c")
        fp = fingerprints(cur)
        captured["fp"] = fp
        since = int((time.time() - DEFER_DAYS * 86400) * 1000)
        captured["unavail"] = unavailable(cur, src, since, sel)
        captured["selected"] = sel
        if sel is not None:
            if not sel:
                raise NothingToExport()
            t0 = time.time()
            proxy = CidFilterCursor(cur, sel)
            if len(a) > 1:
                a[1] = proxy
            else:
                k["c"] = proxy
            res = _fetch(*a, **k)
            keep = {c: v for c, v in res[0].items() if c in sel}
            print(f"incremental: export {len(keep)} convos (fetch_data {time.time() - t0:.1f}s)", flush=True)
            if not keep:
                raise NothingToExport()
            res = (keep,) + tuple(res[1:])
        else:
            res = _fetch(*a, **k)
        captured["convos"] = res[0]
        return res

    data.fetch_data = fetch_record

    def fix_record(contacts):
        out = _fix(contacts)
        for cid, c in out.items():
            mapping[cid] = c.name
        return out

    utils.fix_names = fix_record
    argv = ["sigexport", "--source", src]
    key = stored_key()
    if key:
        argv += ["--key", key]
    sys.argv = argv + [dest]
    rc = 0
    try:
        smain.cli()
    except SystemExit as e:
        rc = e.code or 0
    except NothingToExport:
        rc = 0
    finally:
        Path(dest).mkdir(parents=True, exist_ok=True)
        with open(Path(dest) / "_fingerprints.tsv", "w", encoding="utf-8") as f:
            for cid, v in (captured.get("fp") or {}).items():
                f.write(f"{cid}\t{v}\n")
        with open(Path(dest) / "_selected.tsv", "w", encoding="utf-8") as f:
            sel_ = captured.get("selected")
            f.write("*\n" if sel_ is None else "".join(f"{c}\n" for c in sorted(sel_)))
        with open(Path(dest) / "_unavail.tsv", "w", encoding="utf-8") as f:
            for cid, items in sorted((captured.get("unavail") or {}).items()):
                f.write(f"{cid}\t{format_pending(items)}\n")
        with open(Path(dest) / "_cids.tsv", "w", encoding="utf-8") as f:
            for cid, name in mapping.items():
                if name:
                    f.write(f"{cid}\t{name}\n")
    if out and rc == 0:
        update_conv_map(out, {n: c for c, n in mapping.items() if n}, captured.get("convos"))
    return rc


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "setup-key":
        setup_key(sys.argv[2])
        sys.exit(0)
    if len(sys.argv) >= 4 and sys.argv[1] == "export":
        args = sys.argv[2:]
        full = "--full" in args
        sel = None
        if "--select" in args:
            i = args.index("--select")
            sel = args[i + 1]
            del args[i:i + 2]
        args = [x for x in args if x != "--full"]
        sys.exit(export(args[0], args[1], args[2] if len(args) > 2 else None, sel, full or not sel))
    if len(sys.argv) >= 5 and sys.argv[1] == "precheck":
        sys.exit(precheck(sys.argv[2], sys.argv[3], sys.argv[4]))
    if len(sys.argv) >= 4 and sys.argv[1] == "conv-map":
        conv_map_only(sys.argv[2], sys.argv[3])
        sys.exit(0)
    print(__doc__)
    sys.exit(2)
