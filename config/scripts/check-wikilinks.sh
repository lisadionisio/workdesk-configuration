#!/usr/bin/env bash
# Check note references; historical notes remain valid targets.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/check-wikilinks.py" "$@"
