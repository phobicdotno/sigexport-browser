#!/bin/bash
# Synthetic test: a chat whose data.json lists attachments sigexport could not export
# (missing_kind placeholder + a ref to a file it never wrote) must still be deduplicated
# against another chat, pass the link check, and report missing_reported.
# Usage: tests/test_linkcheck.sh [WORKDIR]   (default: a new mktemp dir)
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd); DD=$HERE/dedup.py
W=${1:-$(mktemp -d)}; rm -rf "$W/root" "$W/state"; mkdir -p "$W/root" "$W/state"
R=$W/root; IDX=$W/state/media_index.tsv; : > "$IDX"
mk(){ # dir chatname file...
  local c="$R/$1/$2"; mkdir -p "$c/media"; shift 2
  : > "$c/chat.md"; : > "$c/data.json"; echo '<html><body>' > "$c/index.html"
  for f in "$@"; do
    printf '[2026-01-01 10:00:00] Test: ![%s](./media/%s)  \n' "$f" "$f" >> "$c/chat.md"
    printf '<img src="./media/%s">\n' "$f" >> "$c/index.html"
    printf '{"date": "2026-01-01T10:00:00", "sender": "Test", "body": "", "attachments": [{"name": "%s", "path": "media/%s", "missing_kind": null}]}\n' "$f" "$f" >> "$c/data.json"
  done
}
mk 001_Alpha "Alpha Test" a1.jpg a2.jpg
printf 'AAAA-same-bytes' > "$R/001_Alpha/Alpha Test/media/a1.jpg"
printf 'unique-alpha' > "$R/001_Alpha/Alpha Test/media/a2.jpg"
mk 002_Beta "Beta Test" b1.jpg gone.jpg
printf 'AAAA-same-bytes' > "$R/002_Beta/Beta Test/media/b1.jpg"   # duplicate of a1.jpg
# gone.jpg is referenced but never written (sigexport: "No file to copy ... skipping!")
printf '{"date": "2026-01-01T11:00:00", "sender": "Test", "body": "", "attachments": [{"name": "", "path": "", "missing_kind": "media"}]}\n' >> "$R/002_Beta/Beta Test/data.json"
printf '[2026-01-01 11:00:00] Test: (media not exported)  \n' >> "$R/002_Beta/Beta Test/chat.md"
printf 'No file to copy at /x/attachments.noindex/zz/gone, skipping!\n' > "$W/state/sig.out"
fail(){ echo "FAIL: $*"; exit 1; }
python3 -I "$DD" dedup "$R" 001_Alpha "$IDX" >/dev/null
python3 -I "$DD" linkcheck "$R" 001_Alpha "$IDX" >/dev/null || fail "alpha linkcheck"
d=$(python3 -I "$DD" dedup "$R" 002_Beta "$IDX"); echo "dedup beta: $d"
lc=$(LC_SIGLOG="$W/state/sig.out" python3 -I "$DD" linkcheck "$R" 002_Beta "$IDX") || fail "beta linkcheck rc!=0: $lc"
echo "linkcheck beta: $lc"
python3 -I -c 'import json,sys;d=json.loads(sys.argv[1]);l=json.loads(sys.argv[2])
assert d["deduped"]==1, d
assert l["missing"]==0 and l["missing_reported"]==2 and l["no_file_lines"]==1, l' "$d" "$lc" || fail "counts"
[ ! -e "$R/002_Beta/Beta Test/media/b1.jpg" ] || fail "duplicate not removed"
grep -q '\.\./\.\./001_Alpha/Alpha Test/media/a1.jpg' "$R/002_Beta/Beta Test/chat.md" || fail "link not rewritten"
# a link broken by dedup must still fail
rm "$R/001_Alpha/Alpha Test/media/a1.jpg"
python3 -I "$DD" linkcheck "$R" 002_Beta "$IDX" >/dev/null && fail "real breakage not detected"
echo "PASS"
