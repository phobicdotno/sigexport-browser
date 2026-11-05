#!/bin/sh
# Thin wrapper: the pipeline is pipeline/export_all.py (macOS, Linux, Windows).
exec python3 "$(dirname "$0")/export_all.py" "$@"
