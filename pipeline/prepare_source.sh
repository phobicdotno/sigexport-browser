#!/bin/bash
# Prepare a read-only working copy of the Signal Desktop data for sigexport.
# Copies config.json + sql/ (small) into WORK_DIR and symlinks the big
# attachments.noindex / stickers.noindex folders. Nothing in the Signal
# folder is modified.
#
# Usage: prepare_source.sh [SIGNAL_DIR] [WORK_DIR]
#   SIGNAL_DIR default: ~/Library/Application Support/Signal (macOS)
#   WORK_DIR   default: ~/SignalExport/_src
set -eu
SIGNAL_DIR=${1:-${SIGNAL_DIR:-$HOME/Library/Application Support/Signal}}
WORK_DIR=${2:-${WORK_DIR:-$HOME/SignalExport/_src}}

[ -f "$SIGNAL_DIR/config.json" ] && [ -d "$SIGNAL_DIR/sql" ] || { echo "Not a Signal Desktop folder: $SIGNAL_DIR" >&2; exit 1; }

if pgrep -xq Signal; then
  echo "Signal Desktop is running. Quitting it so the database copy is consistent..."
  osascript -e 'quit app "Signal"' 2>/dev/null || true
  for _ in $(seq 1 30); do pgrep -xq Signal || break; sleep 1; done
  if pgrep -xq Signal; then echo "WARNING: Signal is still running; the copied database may be inconsistent." >&2; fi
fi

mkdir -p "$WORK_DIR"
rm -rf "$WORK_DIR/sql" "$WORK_DIR/config.json"
cp -p "$SIGNAL_DIR/config.json" "$WORK_DIR/"
cp -Rp "$SIGNAL_DIR/sql" "$WORK_DIR/"
for d in attachments.noindex stickers.noindex; do
  rm -f "$WORK_DIR/$d"
  [ -d "$SIGNAL_DIR/$d" ] && ln -s "$SIGNAL_DIR/$d" "$WORK_DIR/$d"
done
echo "Prepared: $WORK_DIR"
echo "Next: SIGNAL_SRC=\"$WORK_DIR\" $(dirname "$0")/export_all.sh"
