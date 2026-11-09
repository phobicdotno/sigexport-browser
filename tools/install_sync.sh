#!/bin/bash
# Thin wrapper: tools/install_sync.sh [--remote R] [--uninstall] ...  (see install_sync.py --help)
exec "${PY:-/opt/homebrew/bin/python3.13}" "$(dirname "$0")/install_sync.py" "$@"
