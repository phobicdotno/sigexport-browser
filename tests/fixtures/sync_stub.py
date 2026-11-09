#!/usr/bin/env python3
"""Stand-in for pipeline/sync.py with the same CLI/lock/status contract; fakes progress.
SYNC_STUB_SECONDS (default 3) controls the run length. Touches nothing but OUT/_state."""
import argparse, json, os, sys, time
from pathlib import Path
try:
    import fcntl
except ImportError:
    fcntl = None
ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--upload", action="store_true")
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--quit-signal", action="store_true")
a = ap.parse_args()
st = Path(a.out) / "_state"
st.mkdir(parents=True, exist_ok=True)
lock = open(st / "sync.lock", "a")
if fcntl:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(75)
iso = lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z")
started = iso()
def put(**kw):
    d = {"state": "running", "step": "", "progress": 0, "started": started, "finished": None,
         "added_messages": 0, "new_media": 0, "new_chats": 0, "message": ""}
    d.update(kw)
    tmp = st / "sync_status.json.tmp"
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, st / "sync_status.json")
n = float(os.environ.get("SYNC_STUB_SECONDS", "3"))
steps = ["export", "merge", "media", "viewer"] + (["upload"] if a.upload else [])
for i, s in enumerate(steps):
    put(step=s, progress=i / len(steps))
    with open(st / "sync.log", "a") as f:
        f.write("stub step %s\n" % s)
    time.sleep(n / len(steps))
put(state="done", step="done", progress=1, finished=iso(), added_messages=12, new_media=3)
