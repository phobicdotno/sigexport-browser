#!/usr/bin/env python3
"""Run sigexport 3.9.x in-process (run with sigexport's own venv python) for sync.py.

  sync_export.py export SRC DEST   full export of every chat into DEST, plus DEST/_cids.tsv
                                   (conversation id <TAB> sigexport folder name)
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


def export(src, dest, out=None):
    import sigexport.data as data
    import sigexport.main as smain
    import sigexport.utils as utils
    mapping, captured = {}, {}
    _fix, _fetch = utils.fix_names, data.fetch_data

    def fetch_record(*a, **k):
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
    finally:
        Path(dest).mkdir(parents=True, exist_ok=True)
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
        sys.exit(export(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None))
    if len(sys.argv) >= 4 and sys.argv[1] == "conv-map":
        conv_map_only(sys.argv[2], sys.argv[3])
        sys.exit(0)
    print(__doc__)
    sys.exit(2)
