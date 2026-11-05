#!/bin/bash
# sigexport-browser pipeline: export every Signal Desktop chat with sigexport,
# deduplicate media across chats (md5, first copy wins, links rewritten),
# verify links, optionally copy to an rclone remote and verify with
# `rclone check --one-way`. Local exports are never deleted after success.
#
# Configuration (env vars or flags):
#   SIGNAL_SRC      prepared source dir (see prepare_source.sh)      --src DIR
#   OUT_DIR         export destination (default ~/SignalExport)       --out DIR
#   REMOTE          rclone destination, e.g. myremote:Signal-export   --remote R
#                   (empty = no upload, no check)
#   MIN_FREE_GIB    pause new chats while free space is below (6)     --min-free N
#   ABORT_FREE_GIB  abort the current chat when free space drops (4)  --abort-free N
#   TRANSFERS       rclone --transfers (2)                            --transfers N
#   SIGEXPORT       sigexport binary (default: found on PATH)
#   STATE_DIR       logs/state (default $OUT_DIR/_state)
#
# Resumable: re-run the script and it skips chats listed in done.txt/failed.txt.
set -u
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

while [ $# -gt 0 ]; do
  case "$1" in
    --src) SIGNAL_SRC=$2; shift 2;;
    --out) OUT_DIR=$2; shift 2;;
    --remote) REMOTE=$2; shift 2;;
    --min-free) MIN_FREE_GIB=$2; shift 2;;
    --abort-free) ABORT_FREE_GIB=$2; shift 2;;
    --transfers) TRANSFERS=$2; shift 2;;
    -h|--help) sed -n '2,20p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 64;;
  esac
done

SRC=${SIGNAL_SRC:-}
ROOT=${OUT_DIR:-$HOME/SignalExport}
REMOTE=${REMOTE:-}
MIN_FREE_GIB=${MIN_FREE_GIB:-6}
ABORT_FREE_GIB=${ABORT_FREE_GIB:-4}
TRANSFERS=${TRANSFERS:-2}
SIG=${SIGEXPORT:-$(command -v sigexport || true)}
STATE=${STATE_DIR:-$ROOT/_state}
HERE=$(cd "$(dirname "$0")" && pwd)
DD=$HERE/dedup.py

[ -n "$SRC" ] && [ -d "$SRC" ] || { echo "SIGNAL_SRC/--src must point to a prepared source dir (run prepare_source.sh)" >&2; exit 64; }
[ -n "$SIG" ] && [ -x "$SIG" ] || { echo "sigexport not found (pipx install signal-export==3.9.3)" >&2; exit 64; }
if [ -n "$REMOTE" ]; then command -v rclone >/dev/null || { echo "REMOTE set but rclone not installed" >&2; exit 64; }; fi

LOG=$STATE/export_all.log
CHATS=$STATE/chats.txt
DONE=$STATE/done.txt
FAILED=$STATE/failed.txt
MAN=$STATE/manifest.csv
IDX=$STATE/media_index.tsv
STK=$STATE/stickers_done
SIGOUT=$STATE/sigexport_last.out
mkdir -p "$ROOT" "$STATE"; touch "$DONE" "$FAILED" "$IDX"
[ -f "$MAN" ] || echo 'index,chat_name,media_files,deduped,bytes_saved,bytes_kept,upload,check,linkcheck,free_gib_after,missing_reported' > "$MAN"

log(){ echo "$(date '+%F %T') $*" >> "$LOG"; }
free_kb(){ df -k "$ROOT" | awk 'NR==2{print $4}'; }
free_gib(){ echo $(( $(free_kb) / 1048576 )); }
csvq(){ printf '"%s"' "$(printf %s "$1" | sed 's/"/""/g')"; }
jget(){ python3 -I -c 'import sys,json;print(json.loads(sys.argv[1]).get(sys.argv[2],0))' "$1" "$2"; }
upload_manifests(){
  [ -n "$REMOTE" ] || return 0
  rclone copyto "$MAN" "$REMOTE/_manifest/manifest.csv" >> "$LOG" 2>&1
  rclone copyto "$IDX" "$REMOTE/_manifest/media_index.tsv" >> "$LOG" 2>&1
  log "manifests uploaded"
}

if [ ! -s "$CHATS" ]; then
  "$SIG" --source "$SRC" --list-chats 2>/dev/null | python3 -I -c '
import sys
s=sys.stdin.read().rstrip("\n")
print("\n".join(s.split(" | ")))' > "$CHATS"
fi
log "START total_chats=$(wc -l < "$CHATS" | tr -d ' ') free_gib=$(free_gib) remote=${REMOTE:-none}"

wait_disk(){ # pause until MIN_FREE_GIB free
  local i=0
  while [ "$(free_kb)" -lt $((MIN_FREE_GIB*1048576)) ]; do
    [ $((i%10)) -eq 0 ] && log "paused: low disk free_gib=$(free_gib)"
    sleep 60; i=$((i+1))
  done
}

# export_chat name dir -> EXP_RC (0 ok, 1 fail, 2 low-disk abort, 3 blocked => caller exits)
export_chat(){
  local name=$1 w=$2 st=""
  [ -e "$STK" ] && st="--no-stickers"   # stickers are exported once (first chat)
  rm -rf "$w"
  "$SIG" --source "$SRC" --chats "$name" $st "$w" > "$SIGOUT" 2>&1 &
  local pid=$! waited=0
  while kill -0 $pid 2>/dev/null; do
    sleep 10; waited=$((waited+10))
    if [ "$(free_kb)" -lt $((ABORT_FREE_GIB*1048576)) ]; then
      kill $pid 2>/dev/null; wait $pid 2>/dev/null; EXP_RC=2; return; fi
    # nothing written after 10 min: almost always the Keychain prompt is waiting
    if [ $waited -ge 600 ] && [ ! -d "$w" ]; then
      kill $pid 2>/dev/null; wait $pid 2>/dev/null; EXP_RC=3; return; fi
    if [ $waited -ge 14400 ]; then
      kill $pid 2>/dev/null; wait $pid 2>/dev/null; EXP_RC=1; return; fi
  done
  wait $pid; EXP_RC=$?
  [ $EXP_RC -ne 0 ] && EXP_RC=1
}

mark(){ echo "$1,$(csvq "$2"),$3,$4,$5,$6,$7,$8,$9,${10},${11:-0}" >> "$MAN"; }

process(){
  local idx=$1 name=$2 pidx safe d w remote rc
  pidx=$(printf %03d "$idx")
  safe=$(printf %s "$name" | perl -CS -pe 's/[^A-Za-z0-9]+/_/g; $_=substr($_,0,40)')
  d="${pidx}_${safe}"; w="$ROOT/$d"; remote="$REMOTE/$d"
  wait_disk
  log "chat=$idx start dir=$d free_gib=$(free_gib)"
  rm -f "$STATE/present/$d.json"   # pre-dedup media listing is per export
  local t0; t0=$(date +%s)
  export_chat "$name" "$w"
  if [ $EXP_RC -eq 3 ]; then
    log "chat=$idx BLOCKED >10min (Keychain prompt? choose Allow) -> stopping"
    printf '%s\t%s\texport blocked >10min\n' "$idx" "$name" >> "$FAILED"; mark "$idx" "$name" 0 0 0 0 - - - "$(free_gib)"; exit 3
  fi
  if [ $EXP_RC -eq 2 ]; then
    log "chat=$idx free disk <${ABORT_FREE_GIB}GiB mid-export: removing this chat's partial folder only"
    rm -rf "$w"; printf '%s\t%s\tlow disk abort\n' "$idx" "$name" >> "$FAILED"; mark "$idx" "$name" 0 0 0 0 aborted-lowdisk - - "$(free_gib)"; return 0
  fi
  local n_media; n_media=$(find "$w" -path '*/media/*' -type f 2>/dev/null | wc -l | tr -d ' ')
  log "chat=$idx export rc=$EXP_RC secs=$(( $(date +%s)-t0 )) media_files=$n_media"
  if [ $EXP_RC -eq 0 ] && ! ls "$w"/*/chat.md >/dev/null 2>&1; then
    log "chat=$idx empty export"
    rm -rf "$w"; printf '%s\t%s\tempty\n' "$idx" "$name" >> "$DONE"; mark "$idx" "$name" 0 0 0 0 empty - - "$(free_gib)"; return 0
  fi
  if [ $EXP_RC -ne 0 ]; then
    log "chat=$idx FAILED export"; rm -rf "$w"
    printf '%s\t%s\texport failed\n' "$idx" "$name" >> "$FAILED"; mark "$idx" "$name" 0 0 0 0 failed-export - - "$(free_gib)"; return 0
  fi
  [ -e "$STK" ] || touch "$STK"
  # --- dedup + link check (attachments sigexport reported missing - missing_kind in
  # data.json, or files it never wrote ("No file to copy") - are excluded from the
  # check and counted as missing_reported instead of forcing a no-dedup re-export)
  local res lc lcstat=ok lrc
  res=$(python3 -I "$DD" dedup "$ROOT" "$d" "$IDX" 2>> "$LOG"); rc=$?
  if [ $rc -ne 0 ] || [ -z "$res" ]; then lcstat=link_failed; res='{}'; fi
  lc=$(LC_SIGLOG="$SIGOUT" python3 -I "$DD" linkcheck "$ROOT" "$d" "$IDX" 2>> "$LOG"); lrc=$?
  if [ $lrc -ne 0 ] || [ $lcstat != ok ]; then
    log "chat=$idx LINK_FAILED ($lc) -> re-export without dedup"
    lcstat=link_failed
    export_chat "$name" "$w"
    if [ $EXP_RC -ne 0 ]; then
      rm -rf "$w"; printf '%s\t%s\tre-export failed\n' "$idx" "$name" >> "$FAILED"; mark "$idx" "$name" "$n_media" 0 0 0 failed-export - link_failed "$(free_gib)"; return 0
    fi
    res=$(python3 -I "$DD" indexonly "$ROOT" "$d" "$IDX" 2>> "$LOG")
    lc=$(LC_SIGLOG="$SIGOUT" python3 -I "$DD" linkcheck "$ROOT" "$d" "$IDX" 2>> "$LOG") || lcstat=link_failed_nodedup_still_bad
  fi
  local nded saved kept mrep
  nded=$(jget "$res" deduped); saved=$(jget "$res" saved); kept=$(jget "$res" kept)
  mrep=0; [ -n "${lc:-}" ] && mrep=$(jget "$lc" missing_reported 2>/dev/null || echo 0)
  log "chat=$idx dedup media=$n_media linked=$nded bytes_saved=$saved bytes_kept=$kept linkcheck=$lcstat $lc"
  # --- optional upload + verification (copy only, local export is kept)
  local up=skipped ck=skipped
  if [ -n "$REMOTE" ]; then
    local mib u0 us
    mib=$(( $(du -sk "$w" | awk '{print $1}')/1024 )); u0=$(date +%s); up=ok; ck=ok
    perl -e 'alarm 21600; exec @ARGV' rclone copy "$w" "$remote" --transfers "$TRANSFERS" --checkers 8 --retries 8 --low-level-retries 20 --retries-sleep 10s >> "$LOG" 2>&1
    rc=$?; us=$(( $(date +%s)-u0 )); [ $us -lt 1 ] && us=1
    log "chat=$idx upload rc=$rc secs=$us size_mib=$mib"
    if [ $rc -ne 0 ]; then
      up=failed; ck=skipped; printf '%s\t%s\tupload rc=%s\n' "$idx" "$name" "$rc" >> "$FAILED"
    else
      if ! perl -e 'alarm 7200; exec @ARGV' rclone check "$w" "$remote" --one-way --checkers 8 >> "$LOG" 2>&1; then
        ck=failed; printf '%s\t%s\tcheck failed\n' "$idx" "$name" >> "$FAILED"; log "chat=$idx CHECK FAILED"
      else log "chat=$idx check OK (0 differences)"; fi
    fi
  fi
  if [ "$up" != failed ] && [ "$ck" != failed ]; then printf '%s\t%s\n' "$idx" "$name" >> "$DONE"; fi
  mark "$idx" "$name" "$n_media" "$nded" "$saved" "$kept" "$up" "$ck" "$lcstat" "$(free_gib)" "$mrep"
  log "chat=$idx finished free_gib=$(free_gib)"
}

cnt=0; idx=0
while IFS= read -r name; do
  idx=$((idx+1))
  cut -f1 "$DONE" | grep -qx "$idx" && continue
  cut -f1 "$FAILED" | grep -qx "$idx" && continue
  process "$idx" "$name"
  cnt=$((cnt+1)); [ $((cnt%10)) -eq 0 ] && upload_manifests
done < "$CHATS"
log "FINISHED done=$(wc -l < "$DONE" | tr -d ' ') failed=$(wc -l < "$FAILED" | tr -d ' ')"
upload_manifests
echo "Finished. Log: $LOG  Manifest: $MAN"
