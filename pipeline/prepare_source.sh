#!/bin/sh
# Thin wrapper: see pipeline/prepare_source.py (macOS, Linux, Windows).
exec python3 "$(dirname "$0")/prepare_source.py" "$@"
