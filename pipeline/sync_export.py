#!/usr/bin/env python3
"""Run sigexport 3.9.x in-process (run with sigexport's own venv python) for sync.py.

  sync_export.py export SRC DEST   full export of every chat into DEST, plus DEST/_cids.tsv
                                   (conversation id <TAB> sigexport folder name)
  sync_export.py setup-key SRC     one-time, interactive: decrypt the SQLCipher key via
                                   sigexport (Keychain "Signal Safe Storage" prompt) and store
                                   it in the login Keychain as generic password
                                   service=sigexport-browser, account=$USER (ACL: /usr/bin/security)

The key is passed to sigexport in-process (never on a command line, never in a file, never
printed). If the sigexport-browser item is missing, sigexport asks the Keychain itself.
"""
import getpass
import os
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


def export(src, dest):
    import sigexport.data as data
    import sigexport.main as smain
    import sigexport.utils as utils
    mapping = {}
    _fix = utils.fix_names

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
    return rc


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "setup-key":
        setup_key(sys.argv[2])
        sys.exit(0)
    if len(sys.argv) >= 4 and sys.argv[1] == "export":
        sys.exit(export(sys.argv[2], sys.argv[3]))
    print(__doc__)
    sys.exit(2)
