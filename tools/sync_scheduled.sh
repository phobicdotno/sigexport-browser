#!/bin/bash
# Daily scheduled re-sync (run by the LaunchAgent no.phobic.sigexport-browser.sync at 03:00).
# Skips when the Mac runs on battery below MIN_BATTERY percent; logs to $OUT/_state/sync_scheduled.log.
# Env (set in the plist by tools/install_sync.py):
#   OUT          archive folder (default ~/SignalExport)
#   PY           python interpreter (default /opt/homebrew/bin/python3.13)
#   SYNC_PY      path to pipeline/sync.py
#   SYNC_ARGS    extra args (default "--upload --quit-signal")
#   MIN_BATTERY  percent (default 30)
#   PRUNE_SIGNAL_DAYS  prune Signal Desktop attachments older than N days after a verified
#                upload (default 30, 0 = off; sync.py only prunes with --upload + passed rclone check)
OUT="${OUT:-$HOME/SignalExport}"
PY="${PY:-/opt/homebrew/bin/python3.13}"
SYNC_PY="${SYNC_PY:-$(cd "$(dirname "$0")/.." && pwd)/pipeline/sync.py}"
SYNC_ARGS="${SYNC_ARGS:---upload --quit-signal}"
MIN_BATTERY="${MIN_BATTERY:-30}"
export PRUNE_SIGNAL_DAYS="${PRUNE_SIGNAL_DAYS:-30}"
mkdir -p "$OUT/_state"
LOG="$OUT/_state/sync_scheduled.log"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

if command -v pmset >/dev/null 2>&1; then
  B="$(pmset -g batt 2>/dev/null)"
  PCT="$(printf '%s\n' "$B" | grep -Eo '[0-9]+%' | head -1 | tr -d '%')"
  if printf '%s\n' "$B" | grep -q "discharging" && [ -n "$PCT" ] && [ "$PCT" -lt "$MIN_BATTERY" ]; then
    log "skip: on battery at ${PCT}% (< ${MIN_BATTERY}%)"
    exit 0
  fi
fi

log "start: $PY $SYNC_PY --out $OUT $SYNC_ARGS (PRUNE_SIGNAL_DAYS=$PRUNE_SIGNAL_DAYS)"
# shellcheck disable=SC2086
"$PY" "$SYNC_PY" --out "$OUT" $SYNC_ARGS >> "$LOG" 2>&1
RC=$?
if [ "$RC" -eq 75 ]; then log "skip: sync already running (exit 75)"; exit 0; fi
log "end: exit $RC"
exit "$RC"
