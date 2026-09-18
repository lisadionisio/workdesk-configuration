#!/usr/bin/env bash
# session-entry-scan.sh
#
# SessionStart hook. Scans the vault for unprocessed sources and stale
# signal state, writes host-local session-entry.md, and emits a
# concise additionalContext payload that core skills consume.
#
# Output contract (Claude Code SessionStart hook):
#   {"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"..."}}

set -euo pipefail
IFS=$'\n\t'

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT="${CLAUDE_PROJECT_DIR:-$(cd "$DIR/../.." && pwd)}"
# Generated scans belong to the host, never to Obsidian Sync.
STATE_FILE="$(python3 - "$VAULT" <<'STATEPY'
import hashlib, os, sys
from pathlib import Path
key = hashlib.sha256(str(Path(sys.argv[1]).resolve()).encode()).hexdigest()[:16]
base = Path(os.environ.get("WORKDESK_STATE_HOME", str(Path.home()/".local/state/workdesk")))
print(base/key/"session-entry.md")
STATEPY
)"
SIGNALS_STATE="$VAULT/config/state/signals.json"
JSON_GET="$DIR/json-get.sh"

today=$(date '+%Y-%m-%d')
now=$(date '+%Y-%m-%d %H:%M')

# --- scan unprocessed transcripts -----------------------------------------
# Unprocessed transcripts live in system/intake/ (source-kind: transcript,
# processed: false) per the intake → process → archive flow. system/transcripts/
# is the post-processing archive; scan it too as a safety net for files that
# landed there without being flipped to processed: true.
unprocessed_transcripts=()
for dir in "$VAULT/system/intake" "$VAULT/system/transcripts"; do
  [[ -d "$dir" ]] || continue
  while IFS= read -r f; do
    if /usr/bin/grep -q '^source-kind: transcript' "$f" 2>/dev/null \
       && ! /usr/bin/grep -q '^processed: true' "$f" 2>/dev/null; then
      unprocessed_transcripts+=("$f")
    fi
  done < <(/usr/bin/find "$dir" -maxdepth 2 -type f -name '*.md' 2>/dev/null)
done

# --- scan intake ----------------------------------------------------------
intake_items=()
if [[ -d "$VAULT/system/intake" ]]; then
  while IFS= read -r f; do
    intake_items+=("$f")
  done < <(/usr/bin/find "$VAULT/system/intake" -maxdepth 2 -type f -name '*.md' 2>/dev/null)
fi

# --- scan unsummarized session-log raw files ------------------------------
unsummarized=()
if [[ -d "$VAULT/system/session-log" ]]; then
  while IFS= read -r f; do
    if ! /usr/bin/grep -q '^summarized: true' "$f" 2>/dev/null; then
      unsummarized+=("$f")
    fi
  done < <(/usr/bin/find "$VAULT/system/session-log" -maxdepth 1 -type f -name '*-raw.md' 2>/dev/null)
fi

# --- check signal staleness ----------------------------------------------
due_signals=()
signal_diagnostic=""
if due_output="$(python3 "$DIR/signal-due.py" "$SIGNALS_STATE" 2>&1)"; then
  while IFS= read -r signal; do
    [[ -n "$signal" ]] && due_signals+=("$signal")
  done <<< "$due_output"
else
  signal_diagnostic="$due_output"
fi

# --- check for new release (cached 24h, network-tolerant, fail-silent) ----
update_notice=""
update_available="false"
if [[ -x "$DIR/check-for-updates.sh" ]]; then
  update_notice="$("$DIR/check-for-updates.sh" notice 2>/dev/null || true)"
  if [[ -n "$update_notice" ]]; then
    update_available="true"
  fi
fi

# --- write state file -----------------------------------------------------
mkdir -p "$(dirname "$STATE_FILE")"
{
  echo "---"
  echo "last-scan: $now"
  echo "unprocessed:"
  # Strip control chars and quote chars before interpolating filenames
  # into the YAML body — a hostile or simply weird filename should not
  # forge extra frontmatter entries.
  yaml_safe() { printf '%s' "$1" | LC_ALL=C tr -d '\000-\037\177"'; }
  echo "  transcripts:"
  for f in "${unprocessed_transcripts[@]:-}"; do [[ -n "$f" ]] && echo "    - \"$(yaml_safe "$f")\""; done
  echo "  intake:"
  for f in "${intake_items[@]:-}"; do [[ -n "$f" ]] && echo "    - \"$(yaml_safe "$f")\""; done
  echo "  unsummarized-session-logs:"
  for f in "${unsummarized[@]:-}"; do [[ -n "$f" ]] && echo "    - \"$(yaml_safe "$f")\""; done
  echo "due-signals:"
  for s in "${due_signals[@]:-}"; do [[ -n "$s" ]] && echo "  - $s"; done
  echo "update-available: $update_available"
  echo "---"
  echo ""
  echo "# Session Entry Scan ($now)"
  if [[ -n "$update_notice" ]]; then
    echo ""
    echo "$update_notice"
  fi
} > "$STATE_FILE"

# --- emit additionalContext for Claude Code -------------------------------
ctx="WorkDesk OS session entry: "
ctx+="${#unprocessed_transcripts[@]} unprocessed transcripts, "
ctx+="${#intake_items[@]} intake items, "
ctx+="${#unsummarized[@]} unsummarized session logs. "
if (( ${#due_signals[@]} > 0 )); then
  ctx+="Due signals: $(IFS=,; echo "${due_signals[*]}"). "
else
  ctx+="No signals due. "
fi
if [[ -n "$update_notice" ]]; then
  ctx+="$update_notice "
fi
if [[ -n "$signal_diagnostic" ]]; then
  ctx+="Signal readiness unknown: $signal_diagnostic. "
fi
ctx+="See $STATE_FILE for host-local scan state. Historical config/state/session-entry.md is not current runtime state."

# Escape for JSON.
ctx_json=$(printf '%s' "$ctx" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))' 2>/dev/null || printf '"%s"' "$ctx")

cat <<EOF
{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":$ctx_json}}
EOF
exit 0
