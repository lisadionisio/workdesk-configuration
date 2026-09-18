#!/usr/bin/env bash
# pull-gemini-transcripts.sh — Daily ETL pull of Google Meet "Notes by Gemini"
# verbatim transcripts (Tab 2) into system/intake/.
#
# Pure ETL. No AI tokens. Idempotent (skips docs already pulled, by
# gemini-doc-id across system/intake/ and system/transcripts/).
#
# Why a separate script from pull-google-transcripts.sh:
#
#   Meet generates TWO kinds of artifacts in Drive per recorded meeting:
#     1. A standalone Doc literally named "<Title> - YYYY/MM/DD HH:MM TZ - Transcript"
#        — full verbatim, single-tab.  pull-google-transcripts.sh handles these.
#     2. A "Notes by Gemini" Doc with TWO tabs: Notes (AI summary) + Transcript
#        (full verbatim).  Drive's text/plain export only returns the first tab,
#        so it misses the verbatim entirely.  This script reaches it via the
#        Docs API with `includeTabsContent: true` and extracts only the
#        Transcript tab.
#
#   Meetings vary: some get only (1), some only (2), some both, some neither.
#   Running both scripts catches every transcribed meeting Workspace produced.
#
# Source enumeration goes through Calendar (not Drive search) because the
# Gemini Docs don't have a stable name pattern — they're titled after the
# meeting and look identical to user-authored docs.  The calendar attachment
# `title: "Notes by Gemini"` is the only reliable signal.
#
# Usage:
#   bash config/scripts/pull-gemini-transcripts.sh --account you@example.com                         # default: --days 1
#   bash config/scripts/pull-gemini-transcripts.sh --account you@example.com --days 7
#   bash config/scripts/pull-gemini-transcripts.sh --account you@example.com --days 30 --backfill    # >7 requires --backfill
#   bash config/scripts/pull-gemini-transcripts.sh --account you@example.com --dry-run
#   bash config/scripts/pull-gemini-transcripts.sh --account you@example.com --doc-id <id> --force
#   # After reviewing exact extracted text: --doc-id <id> --force --reviewed-short-sha256 <sha256>
#   bash config/scripts/pull-gemini-transcripts.sh --account you@example.com --status
#   bash config/scripts/pull-gemini-transcripts.sh --account you@example.com --help
#
# Exit codes:
#   0  success (may include zero new pulls)
#   1  partial — some docs failed to pull
#   2  hard failure (auth, bad args, API unreachable)

set -uo pipefail
umask 077
ORIGINAL_ARGS=("$@")

# ── Constants ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INTAKE_DIR="$VAULT_ROOT/system/intake"
TRANSCRIPTS_DIR="$VAULT_ROOT/system/transcripts"
STATE_FILE=""
LOG_FILE=""
SOURCE_FORMAT="gemini-meet-transcript"

# Text below this byte threshold requires review. It may be a short genuine
# transcript, a provider notice, or an unsupported extraction layout; length
# alone does not establish the cause. Such sources do not count as full coverage.
MIN_TRANSCRIPT_CHARS=500

# ── Defaults ─────────────────────────────────────────────────────────────────
DAYS=1
BACKFILL=0
DRY_RUN=0
FORCE_DOC_ID=""
FORCE=0
REVIEWED_SHORT_SHA256=""
SHOW_STATUS=0
ACCOUNT="${WORKDESK_GWS_ACCOUNT:-}"
ACCOUNT_FLAG=0

# ── Helpers ──────────────────────────────────────────────────────────────────
log() {
  local ts
  mkdir -p "$(dirname "$LOG_FILE")" || return 1
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  printf '%s %s\n' "$ts" "$*" | tee -a "$LOG_FILE" >&2
}

slug_from_title() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' \
    | cut -c1-80
}

# Convert a validated Calendar start to the operator-local calendar date.
# All-day values already represent a date; timestamps must include an offset.
iso_to_local_date() {
  "${WORKDESK_PYTHON:-python3}" - "$1" <<'PYDATE'
import datetime as dt, re, sys
value = sys.argv[1]
try:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        print(dt.date.fromisoformat(value).isoformat())
    elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})", value):
        print(dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().date().isoformat())
    else:
        raise ValueError()
except ValueError:
    sys.exit(1)
PYDATE
}

write_state() {
  local content="$1"
  [[ $DRY_RUN -eq 1 ]] && return 0
  mkdir -p "$STATE_DIR" || exit 2
  local tmp
  tmp="$(mktemp "$STATE_DIR/.checkpoint-XXXXXXXX")" || exit 2
  local prior=/dev/null coverage_start
  [[ -f "$STATE_FILE" ]] && prior="$STATE_FILE"
  coverage_start="${CUTOFF_ISO:-$(date -u -r "$(( $(date -u +%s) - DAYS * 86400 ))" '+%Y-%m-%dT%H:%M:%SZ')}"
  printf '%s\n' "$content" | jq --arg account "$ACCOUNT" --arg start "$coverage_start" --slurpfile prior "$prior" '
    ($prior[0] // {}) as $old | $old + . + {account: $account,
      coverage_start_at: ($old.coverage_start_at // $start)}
  ' > "$tmp" || exit 2
  mv "$tmp" "$STATE_FILE" || exit 2
}

record_enumeration_failure() {
  local reason="$1" now fails
  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  fails="$(read_state_field consecutive_failures 0)"
  write_state "$(jq -n --arg now "$now" --arg reason "$reason" --argjson fails "$((fails + 1))" '
    {last_failure_at:$now, last_run_at:$now, last_run_pulled:0,
     last_failure_reason:$reason, consecutive_failures:$fails}')"
}

read_state_field() {
  local field="$1"
  local default="$2"
  if [[ -f "$STATE_FILE" ]]; then
    jq -r --arg field "$field" --arg default "$default" \
      '(.[$field] // $default)' "$STATE_FILE" 2>/dev/null \
      || printf '%s' "$default"
  else
    printf '%s' "$default"
  fi
}

# ── Parse args ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --account)
      [[ $# -ge 2 && -n "$2" && $ACCOUNT_FLAG -eq 0 ]] || { echo "Specify --account once with an email" >&2; exit 2; }
      ACCOUNT="$2"; ACCOUNT_FLAG=1; shift 2 ;;
    --days)     [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]*$ ]] || { echo "--days requires a positive integer" >&2; exit 2; }; DAYS="$2"; shift 2 ;;
    --backfill) BACKFILL=1; shift ;;
    --dry-run)  DRY_RUN=1; shift ;;
    --doc-id)   [[ $# -ge 2 && "$2" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "--doc-id requires a document ID" >&2; exit 2; }; FORCE_DOC_ID="$2"; shift 2 ;;
    --force)    FORCE=1; shift ;;
    --reviewed-short-sha256)
      [[ $# -ge 2 && "$2" =~ ^[a-f0-9]{64}$ && -z "$REVIEWED_SHORT_SHA256" ]] || { echo "Specify --reviewed-short-sha256 once with a lowercase SHA-256" >&2; exit 2; }
      REVIEWED_SHORT_SHA256="$2"; shift 2 ;;
    --status)   SHOW_STATUS=1; shift ;;
    --help|-h)
      sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown flag: $1" >&2
      echo "Try: $0 --help" >&2
      exit 2
      ;;
  esac
done

# Do not use a shell-default account, even when it currently works.
if [[ ! "$ACCOUNT" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
  echo "ERROR: specify --account email or WORKDESK_GWS_ACCOUNT" >&2
  exit 2
fi
ACCOUNT="$(printf '%s' "$ACCOUNT" | tr '[:upper:]' '[:lower:]')"
VAULT_KEY="$(printf '%s' "$VAULT_ROOT" | shasum -a 256 | cut -c1-16)"
ACCOUNT_KEY="$(printf '%s' "$ACCOUNT" | shasum -a 256 | cut -c1-32)"
STATE_DIR="${WORKDESK_STATE_HOME:-$HOME/.local/state/workdesk}/$VAULT_KEY/gemini-transcripts/$ACCOUNT_KEY"
STATE_FILE="$STATE_DIR/pull-gemini.json"
LOG_FILE="$STATE_DIR/pull-gemini-transcripts.log"

# Serialize both account routes before reading any mutable checkpoint. The host
# lock is deliberately outside the account directory, and is never removed.
# Read-only status can inspect atomically published checkpoints without locking.
if [[ $SHOW_STATUS -eq 0 ]]; then
  IMPORT_LOCK="${WORKDESK_STATE_HOME:-$HOME/.local/state/workdesk}/$VAULT_KEY/gemini-transcripts/import.lock"
  if [[ -z "${WORKDESK_GEMINI_LOCK_FD:-}" ]]; then
    exec "${WORKDESK_PYTHON:-python3}" "$SCRIPT_DIR/lib/gemini_import_lock.py" run "$IMPORT_LOCK" "$0" "${ORIGINAL_ARGS[@]}"
  fi
  "${WORKDESK_PYTHON:-python3}" "$SCRIPT_DIR/lib/gemini_import_lock.py" verify "$IMPORT_LOCK" || exit 2
fi

# Refuse damaged checkpoints rather than resetting history or widening a query.
if [[ -e "$STATE_FILE" || -L "$STATE_FILE" ]]; then
  if [[ ! -f "$STATE_FILE" || -L "$STATE_FILE" ]] || ! jq -e --arg account "$ACCOUNT" '
    type == "object" and .account == $account and
    ((.last_success_at == null) or (.last_success_at | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))) and
    ((.consecutive_failures == null) or (.consecutive_failures | type == "number" and . >= 0 and floor == .)) and
    ((.coverage_start_at == null) or (.coverage_start_at | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))) and
    ((.unresolved_sources == null) or (.unresolved_sources | type == "array" and all(.[];
      type == "object" and (.document_id | type == "string") and
      (.record | type == "array" and length == 5 and
        (.[0] | type == "string" and test("^[A-Za-z0-9_-]+$")) and
        (.[1] | type == "string" and test("[^[:space:]]")) and
        (.[2] | type == "string" and length > 0) and
        (.[3] | type == "string") and
        (.[4] | type == "array" and all(.[]; type == "object"))) and .record[0] == .document_id))) and
    (([.unresolved_sources[]?.document_id] | length) == ([.unresolved_sources[]?.document_id] | unique | length))
  ' "$STATE_FILE" >/dev/null 2>&1; then
    printf 'ERROR: invalid checkpoint; preserved for reconciliation\n' >&2
    exit 2
  fi
  for checkpoint_field in last_success_at coverage_start_at; do
    checkpoint_date="$(jq -r --arg field "$checkpoint_field" '.[$field] // empty' "$STATE_FILE")"
    if [[ -n "$checkpoint_date" ]] && ! iso_to_local_date "$checkpoint_date" >/dev/null 2>&1; then
      printf 'ERROR: invalid checkpoint date; preserved for reconciliation\n' >&2
      exit 2
    fi
  done
fi

# ── --status ────────────────────────────────────────────────────────────────
if [[ $SHOW_STATUS -eq 1 ]]; then
  echo "Gemini Meet transcripts pull status for $ACCOUNT:"
  if [[ -f "$STATE_FILE" ]]; then
    jq -r '
      "  last_success_at:    \(.last_success_at // "never")",
      "  last_failure_at:    \(.last_failure_at // "never")",
      "  consecutive_fails:  \(.consecutive_failures // 0)",
      "  files_last_run:     \(.last_run_pulled // 0)",
      "  unresolved_sources: \((.unresolved_sources // []) | length)",
      "  last_run_at:        \(.last_run_at // "never")"
    ' "$STATE_FILE"
    unresolved_count="$(jq '(.unresolved_sources // []) | length' "$STATE_FILE")"
    failures="$(jq '.consecutive_failures // 0' "$STATE_FILE")"
    jq -r '.unresolved_sources[]? | "  source \(.document_id): " +
      (if .result == 6 then "missing Transcript tab; retained for retry"
       elif .result == 1 then "short transcript; content review required"
       elif .result == 5 then "document unavailable; cause unverified"
       else "import failure; inspect log" end)' "$STATE_FILE"
    if [[ $unresolved_count -gt 0 || $failures -gt 0 ]]; then
      echo "  HEALTH: INCOMPLETE (unresolved sources or failed attempts)"
      exit 0
    fi
    last_success="$(jq -r '.last_success_at // empty' "$STATE_FILE")"
    if [[ -n "$last_success" ]]; then
      last_epoch="$(TZ=UTC date -j -f "%Y-%m-%dT%H:%M:%SZ" "${last_success%.*Z}Z" "+%s" 2>/dev/null || echo 0)"
      now_epoch="$(date -u "+%s")"
      delta_h=$(( (now_epoch - last_epoch) / 3600 ))
      echo "  hours_since_success: $delta_h"
      if [[ $delta_h -gt 36 ]]; then
        echo "  HEALTH: STALE (last success > 36h ago)"
      else
        echo "  HEALTH: ok"
      fi
    else
      echo "  HEALTH: UNKNOWN (no successful enumeration recorded)"
    fi
  else
    echo "  (no state file — script has never run successfully)"
  fi
  exit 0
fi

# ── Validate args ───────────────────────────────────────────────────────────
if [[ -n "$FORCE_DOC_ID" && $FORCE -eq 0 ]]; then
  echo "Error: --doc-id requires --force" >&2
  exit 2
fi

if [[ "$DAYS" -gt 7 && $BACKFILL -eq 0 && -z "$FORCE_DOC_ID" ]]; then
  echo "Error: --days $DAYS > 7 requires --backfill" >&2
  exit 2
fi

if [[ -n "$REVIEWED_SHORT_SHA256" && ( -z "$FORCE_DOC_ID" || $FORCE -eq 0 ) ]]; then
  echo "Error: reviewed short content requires --doc-id and --force" >&2
  exit 2
fi

# Auto-extend lookback if last success was older than --days
last_success_at="$(read_state_field "last_success_at" "")"
lookback_at="$last_success_at"
[[ -n "$lookback_at" ]] || lookback_at="$(read_state_field "coverage_start_at" "")"
if [[ -n "$lookback_at" && -z "$FORCE_DOC_ID" && $BACKFILL -eq 0 ]]; then
  last_epoch="$(TZ=UTC date -j -f "%Y-%m-%dT%H:%M:%SZ" "$lookback_at" "+%s" 2>/dev/null || echo 0)"
  now_epoch="$(date -u "+%s")"
  delta_days=$(( (now_epoch - last_epoch + 86399) / 86400 ))
  if [[ $delta_days -gt $DAYS ]]; then
    log "INFO   coverage requires $delta_days days; extending lookback from $DAYS to $delta_days"
    DAYS=$delta_days
  fi
fi

mkdir -p "$INTAKE_DIR" "$(dirname "$STATE_FILE")"

# ── Pre-flight ──────────────────────────────────────────────────────────────
GWS_BIN="${WORKDESK_GWS_BIN:-$(type -P gws || true)}"
if [[ "$GWS_BIN" != /* || ! -x "$GWS_BIN" ]]; then
  log "ERROR  gws CLI not on PATH"
  exit 2
fi

# Route every direct CLI call through identity verification. No shell startup
# file is assumed by cron or launchd, and no credential value is exported.
gws() {
  "${WORKDESK_PYTHON:-python3}" "$SCRIPT_DIR/lib/gws_account.py" "$GWS_BIN" --account "$ACCOUNT" "$@"
}

# ── gws state gate ──────────────────────────────────────────────────────────
# Verify access through the supported CLI read below. Filesystem layouts differ
# across gws versions and do not prove that the active credential store is usable.
# Do not inspect or export credentials, or reject a working keyring-backed login
# merely because an older state directory is present or missing.
if ! gws drive about get --params '{"fields": "user(emailAddress)"}' >/dev/null 2>&1; then
  log "ERROR  Google account verification failed; check the explicit account route and dedicated login flow"
  prev_fails="$(read_state_field "consecutive_failures" "0")"
  new_fails=$(( prev_fails + 1 ))
  write_state "$(jq -n \
    --arg last_failure "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
    --argjson fails "$new_fails" \
    --arg prev_success "$last_success_at" \
    '{
       last_success_at: (if $prev_success == "" then null else $prev_success end),
       last_failure_at: $last_failure,
       last_failure_reason: "account-verification",
       consecutive_failures: $fails,
       last_run_at: $last_failure,
       last_run_pulled: 0
     }')"
  exit 2
fi

# ── Cutoff ──────────────────────────────────────────────────────────────────
CUTOFF_EPOCH=$(( $(date -u "+%s") - DAYS * 86400 ))
CUTOFF_ISO="$(date -u -r "$CUTOFF_EPOCH" "+%Y-%m-%dT%H:%M:%SZ")"
NOW_ISO="$(date -u "+%Y-%m-%dT%H:%M:%SZ")"

log "INFO   starting Gemini Meet pull (days=$DAYS dry_run=$DRY_RUN force_doc=${FORCE_DOC_ID:-none} cutoff=$CUTOFF_ISO)"

# ── Write intake file for one Gemini Doc ────────────────────────────────────
# Args: <doc_id> <event_title> <event_start_iso> <event_organizer> <attendees_json>
write_intake_for_doc() {
  local doc_id="$1"
  local event_title="$2"
  local event_start="$3"
  local event_organizer="$4"
  local attendees_json="$5"

  # A force flag selects a single source; it never authorizes replacement or
  # duplication of a source already present in intake or the transcript archive.
  local identity_result
  "${WORKDESK_PYTHON:-python3}" "$SCRIPT_DIR/lib/gemini_source_identity.py" "$doc_id" "$INTAKE_DIR" "$TRANSCRIPTS_DIR" >/dev/null
  identity_result=$?
  if [[ $identity_result -gt 1 ]]; then
    log "ERROR  source identity inventory requires reconciliation"
    return 4
  fi
  if [[ $identity_result -eq 0 ]]; then
    if [[ $FORCE -eq 1 && "$FORCE_DOC_ID" == "$doc_id" ]]; then
      log "ERROR  $doc_id source already exists; explicit reconciliation required"
      return 4
    fi
    log "SKIP   $doc_id already-pulled (pre-fetch)"
    return 2
  fi

  # Fetch the Doc with all tab content.  Capture output unconditionally —
  # gws prints the API error JSON to stdout AND exits non-zero on 403/404,
  # so we need to inspect the body regardless of exit code. A 403/404 leaves
  # the source unresolved; it does not prove an ownership or sharing cause.
  local doc_json doc_exit=0
  doc_json="$(mktemp)" || return 4
  gws docs documents get \
    --params "$(jq -n --arg id "$doc_id" '{documentId:$id, includeTabsContent:true}')" \
    --format json > "$doc_json" 2>/dev/null || doc_exit=$?

  if [[ ! -s "$doc_json" ]]; then
    log "ERROR  $doc_id docs.get returned empty (gws or network failure)"
    rm -f "$doc_json"
    return 4
  fi

  local api_err_code
  api_err_code="$(jq -r '.error.code // empty' "$doc_json" 2>/dev/null)"
  if [[ -n "$api_err_code" ]]; then
    if [[ "$api_err_code" == "403" || "$api_err_code" == "404" ]]; then
      log "SKIP   $doc_id unavailable (code=$api_err_code; cause not established) title=\"$event_title\""
      rm -f "$doc_json"
      return 5
    fi
    log "ERROR  $doc_id docs.get api error code=$api_err_code"
    rm -f "$doc_json"
    return 4
  fi

  if [[ $doc_exit -ne 0 ]] || ! jq -e --arg id "$doc_id" '
    type == "object" and (has("error") | not) and .documentId == $id and
    (.tabs | type == "array")
  ' "$doc_json" >/dev/null 2>&1; then
    log "ERROR  $doc_id document response failed or identity did not match"
    rm -f "$doc_json"
    return 4
  fi

  # Preserve text-run bytes, including speaker boundaries and trailing newlines.
  # Shell command substitution and line-oriented filters would strip them.
  local transcript_file
  transcript_file="$(mktemp)" || return 4
  local extract_result=0
  "${WORKDESK_PYTHON:-python3}" "$SCRIPT_DIR/lib/gemini_document_text.py" "$doc_json" "$doc_id" > "$transcript_file" || extract_result=$?
  if [[ $extract_result -ne 0 ]]; then
    rm -f "$doc_json" "$transcript_file"
    if [[ $extract_result -eq 3 ]]; then
      log "GAP    $doc_id no Transcript tab returned; retain for retry, never substitute summary"
      return 6
    fi
    log "ERROR  $doc_id invalid document response"
    return 4
  fi

  local size
  size="$(wc -c < "$transcript_file" | tr -d ' ')"

  local transcript_sha256
  transcript_sha256="$(shasum -a 256 "$transcript_file" | cut -d ' ' -f 1)"
  if [[ -n "$REVIEWED_SHORT_SHA256" && "$transcript_sha256" != "$REVIEWED_SHORT_SHA256" ]]; then
    log "ERROR  $doc_id reviewed transcript checksum mismatch; source not published"
    rm -f "$doc_json" "$transcript_file"
    return 4
  fi
  if [[ $size -lt $MIN_TRANSCRIPT_CHARS && -z "$REVIEWED_SHORT_SHA256" ]]; then
    log "SKIP   $doc_id short-transcript-needs-review size=${size}b title=\"$event_title\""
    rm -f "$doc_json" "$transcript_file"
    return 1
  fi

  # Derive filename
  local local_date slug filename target_path date_value heading_date
  if [[ -z "$event_start" && "$FORCE_DOC_ID" == "$doc_id" ]]; then
    local_date="undated"; date_value="null"; heading_date="meeting date unknown"
  else
    local_date="$(iso_to_local_date "$event_start")" || { log "ERROR  $doc_id invalid calendar start; no date inferred"; rm -f "$doc_json" "$transcript_file"; return 4; }
    date_value="$local_date"; heading_date="$local_date"
  fi
  slug="$(slug_from_title "$event_title")"
  [[ -z "$slug" ]] && slug="$(printf 'untitled-%s' "${doc_id:0:8}" | tr 'A-Z' 'a-z')"
  filename="${local_date}-${slug}.md"
  target_path="$INTAKE_DIR/$filename"

  # Recheck source identity after fetching. Text in an occupied filename is
  # never used as identity evidence; only the frontmatter inventory decides.
  "${WORKDESK_PYTHON:-python3}" "$SCRIPT_DIR/lib/gemini_source_identity.py" "$doc_id" "$INTAKE_DIR" "$TRANSCRIPTS_DIR" >/dev/null
  identity_result=$?
  if [[ $identity_result -ne 1 ]]; then
    log "ERROR  source identity changed or needs review after fetch"
    rm -f "$doc_json" "$transcript_file"
    return 4
  fi
  if [[ -e "$target_path" || -L "$target_path" ]]; then
    local short_suffix="${doc_id:0:6}"
    filename="${local_date}-${slug}-${short_suffix}.md"
    target_path="$INTAKE_DIR/$filename"
    log "INFO   filename collision uses suffix; publication still refuses replacement"
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    log "DRY    $doc_id WOULD pull → $filename (\"$event_title\" $local_date, ${size}b)"
    rm -f "$doc_json" "$transcript_file"
    return 3
  fi

  # Preserve calendar invitees as supplied; invitations do not establish
  # presence, and an email local part is not an inferred person's name.
  local invitees_json
  invitees_json="$(printf '%s' "$attendees_json" | jq -ce '
    if type == "array" and all(.[]; type == "object") then .
    else error("Invalid calendar invitees") end
  ')" || { log "ERROR  $doc_id invalid calendar invitees"; rm -f "$doc_json" "$transcript_file"; return 4; }

  local pulled_at drive_url
  pulled_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  drive_url="https://docs.google.com/document/d/$doc_id"

  local tmp
  tmp="$(mktemp "$INTAKE_DIR/.gemini-source-XXXXXXXX")" || { rm -f "$doc_json" "$transcript_file"; return 4; }
  {
    printf -- '---\n'
    printf -- 'type: source\n'
    printf -- 'source-kind: transcript\n'
    printf -- 'date: %s\n' "$date_value"
    printf -- 'processed: false\n'
    printf -- 'processed-into: []\n'
    printf -- 'title: %s\n' "$(printf '%s' "$event_title" | jq -Rs '.')"
    printf -- 'gemini-doc-id: %s\n' "$doc_id"
    printf -- 'gemini-doc-url: %s\n' "$drive_url"
    printf -- 'event-start: %s\n' "$(printf '%s' "$event_start" | jq -Rs 'if . == "" then null else . end')"
    printf -- 'event-organizer: %s\n' "$(printf '%s' "$event_organizer" | jq -Rs 'if . == "" then null else . end')"
    printf -- 'attendees-from-source: []\n'
    printf -- 'calendar-invitees: %s\n' "$invitees_json"
    printf -- 'source-format: %s\n' "$SOURCE_FORMAT"
    if [[ -n "$REVIEWED_SHORT_SHA256" ]]; then
      printf -- 'short-transcript-review: exact-content-reviewed\n'
      printf -- 'reviewed-transcript-sha256: %s\n' "$transcript_sha256"
    fi
    printf -- 'pulled-at: %s\n' "$pulled_at"
    printf -- '---\n\n'
    printf -- '# %s — %s (raw transcript)\n\n' "$event_title" "$heading_date"
    printf -- 'Verbatim transcript extracted from the "Notes by Gemini" Google Doc, **Transcript** tab. Speakers are name-resolved by Google (e.g., "Jane Doe: ..."). The Gemini-generated summary on the Notes tab is intentionally NOT pulled per [[../../config/rules/source-processing-pattern]] — synthesis happens at processing time from the verbatim, not from another system'"'"'s summary.\n\n'
    printf -- 'Calendar invitees are source metadata, not evidence of attendance. Determine participation from the transcript.\n\n'
    printf -- '## Transcript\n\n'
    cat "$transcript_file"
  } > "$tmp" || { log "ERROR  $doc_id staging failed; candidate retained: $tmp"; rm -f "$doc_json" "$transcript_file"; return 4; }

  # Atomic no-replace publication also covers a competing writer arriving
  # after the collision check. Retain the candidate when reconciliation is needed.
  if ! "${WORKDESK_PYTHON:-python3}" - "$tmp" "$target_path" <<'PYPUBLISH'
import os, sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
try:
    with source.open('rb') as handle:
        os.fsync(handle.fileno())
    os.link(source, target)
except OSError:
    print('Source publication refused; existing destination and candidate preserved.', file=sys.stderr)
    sys.exit(1)
source.unlink()
PYPUBLISH
  then
    log "ERROR  $doc_id publication failed; reconcile staged candidate: $tmp"
    rm -f "$doc_json" "$transcript_file"
    return 4
  fi
  rm -f "$doc_json" "$transcript_file"

  log "PULL   $doc_id → $filename (${size}b) \"$event_title\""
  return 0
}

# ── Forced single-doc path ──────────────────────────────────────────────────
if [[ -n "$FORCE_DOC_ID" ]]; then
  # No calendar context — pull minimal info from the Doc itself
  if ! meta="$(gws docs documents get --params \
    "$(jq -n --arg id "$FORCE_DOC_ID" '{documentId:$id, includeTabsContent:false}')" \
    --format json 2>/dev/null)" || ! printf '%s' "$meta" | jq -e --arg id "$FORCE_DOC_ID" '
      type == "object" and (has("error") | not) and .documentId == $id and
      (.title | type == "string" and length > 0)
    ' >/dev/null 2>&1; then
    log "ERROR  $FORCE_DOC_ID metadata fetch failed"
    exit 2
  fi
  title="$(printf '%s' "$meta" | jq -r '.title')"
  write_intake_for_doc "$FORCE_DOC_ID" "$title" "" "" "[]"
  rc=$?
  # A single document is not evidence of complete calendar enumeration.
  # Preserve the existing watermark, including on dry-run and failure.
  case "$rc" in
    0|2|3) exit 0 ;;
    *) log "ERROR  single-document pull incomplete (result=$rc); checkpoint preserved"; exit 1 ;;
  esac
fi

# ── List calendar events with Notes-by-Gemini attachments ───────────────────
TIME_MAX_ISO="$NOW_ISO"
PARAMS_JSON="$(jq -n --arg tmin "$CUTOFF_ISO" --arg tmax "$TIME_MAX_ISO" \
  '{calendarId:"primary", timeMin:$tmin, timeMax:$tmax, singleEvents:true, orderBy:"startTime", maxResults:250}')"

events_json="$(mktemp)"
trap 'rm -f "$events_json"' EXIT

# Finish and validate every page before publishing any transcript source.
fetch_calendar_pages() {
  local page token="" params count=0 next merged seen
  page="$(mktemp)" || return 1
  merged="$(mktemp)" || { rm -f "$page"; return 1; }
  seen="$(mktemp)" || { rm -f "$page" "$merged"; return 1; }
  printf '{"items":[]}\n' > "$events_json"
  while :; do
    count=$((count + 1))
    if [[ $count -gt 10000 ]]; then rm -f "$page" "$merged" "$seen"; return 1; fi
    params="$(printf '%s' "$PARAMS_JSON" | jq --arg token "$token" 'if $token == "" then . else . + {pageToken:$token} end')"
    if ! gws calendar events list --params "$params" --format json > "$page" 2>/dev/null || ! jq -e '
      type == "object" and (has("error") | not) and
      ((has("items") | not) or (.items | type == "array" and all(.[]; type == "object"))) and
      ((has("nextPageToken") | not) or (.nextPageToken | type == "string" and length > 0 and (test("[\u0000-\u001f]") | not)))
    ' "$page" >/dev/null 2>&1; then
      rm -f "$page" "$merged" "$seen"; return 1
    fi
    jq -s '{items: (.[0].items + (.[1].items // []))}' "$events_json" "$page" > "$merged" || { rm -f "$page" "$merged" "$seen"; return 1; }
    cat "$merged" > "$events_json" || { rm -f "$page" "$merged" "$seen"; return 1; }
    next="$(jq -r '.nextPageToken // empty' "$page")"
    [[ -z "$next" ]] && break
    if grep -Fxq -- "$next" "$seen"; then rm -f "$page" "$merged" "$seen"; return 1; fi
    printf '%s\n' "$next" >> "$seen"
    token="$next"
  done
  rm -f "$page" "$merged" "$seen"
}

if ! fetch_calendar_pages; then
  log "ERROR  gws calendar events list failed"
  prev_fails="$(read_state_field "consecutive_failures" "0")"
  new_fails=$(( prev_fails + 1 ))
  write_state "$(jq -n \
    --arg now "$NOW_ISO" \
    --argjson fails "$new_fails" \
    --arg prev_success "$last_success_at" \
    '{
       last_success_at: (if $prev_success == "" then null else $prev_success end),
       last_failure_at: $now,
       last_failure_reason: "calendar-enumeration",
       consecutive_failures: $fails,
       last_run_at: $now,
       last_run_pulled: 0
     }')"
  exit 2
fi

# Preserve each attachment record as JSON; TSV escaping corrupts quoted names.
docs_tsv="$(mktemp)"
if ! jq -c '
  .items[]?
  | if (.attachments == null) then empty
    elif (.attachments | type == "array" and all(.[]; type == "object")) then .
    else error("Invalid calendar attachments") end
  | . as $e
  | .attachments[]
  | select(.title == "Notes by Gemini")
  | if (.fileId | type == "string" and test("^[A-Za-z0-9_-]+$")) and
      ($e.summary | type == "string" and test("[^[:space:]]")) and
      ($e.start | type == "object") and
      (([$e.start.dateTime, $e.start.date] | map(select(. != null)) | length) == 1) and
      (($e.start.dateTime // $e.start.date) | type == "string" and length > 0) and
      (($e.organizer == null) or ($e.organizer | type == "object")) and
      (($e.organizer.email == null) or ($e.organizer.email | type == "string")) and
      (($e.attendees == null) or ($e.attendees | type == "array" and all(.[]; type == "object")))
    then [.fileId, $e.summary, ($e.start.dateTime // $e.start.date), ($e.organizer.email // ""), ($e.attendees // [])]
    else error("Incomplete or malformed Gemini calendar metadata") end
' "$events_json" > "$docs_tsv"; then
  log "ERROR  malformed calendar attachment metadata; success preserved"
  record_enumeration_failure "calendar-metadata"
  rm -f "$docs_tsv"
  exit 2
fi

# Replay unresolved sources even if no longer attached in the current query.
# Current calendar metadata takes precedence, but conflicting current attachments
# for one source ID require reconciliation instead of selecting an arbitrary date.
prior_state=/dev/null
[[ -f "$STATE_FILE" ]] && prior_state="$STATE_FILE"
merged_records="$(mktemp)" || exit 2
if ! jq -nc --slurpfile prior "$prior_state" --slurpfile current "$docs_tsv" '
  (reduce $current[] as $r ({};
    if has($r[0]) and .[$r[0]] != $r then error("Conflicting calendar source records")
    else .[$r[0]] = $r end)) as $fresh |
  (reduce (($prior[0].unresolved_sources // [])[] | .record) as $r ({}; .[$r[0]] = $r)) + $fresh | .[]
' > "$merged_records"; then
  log "ERROR  unresolved-source replay metadata requires reconciliation"
  record_enumeration_failure "replay-metadata"
  rm -f "$docs_tsv" "$merged_records"
  exit 2
fi
mv "$merged_records" "$docs_tsv" || exit 2

total_found=$(wc -l < "$docs_tsv" | tr -d ' ')
log "INFO   reviewing $total_found Gemini sources from calendar and retained recovery records since $CUTOFF_ISO"

pulled=0
skipped=0
stub=0
no_access=0
missing_transcript=0
failed=0
unresolved_sources='[]'

while IFS= read -r record; do
  [[ -z "$record" ]] && continue
  doc_id="$(printf '%s' "$record" | jq -r '.[0]')"
  event_title="$(printf '%s' "$record" | jq -r '.[1]')"
  event_start="$(printf '%s' "$record" | jq -r '.[2]')"
  event_organizer="$(printf '%s' "$record" | jq -r '.[3]')"
  attendees_json="$(printf '%s' "$record" | jq -c '.[4]')"
  write_intake_for_doc "$doc_id" "$event_title" "$event_start" "$event_organizer" "$attendees_json"
  source_result=$?
  case $source_result in
    0) pulled=$((pulled + 1)) ;;
    1) stub=$((stub + 1)) ;;
    2) skipped=$((skipped + 1)) ;;
    3) pulled=$((pulled + 1)) ;;
    5) no_access=$((no_access + 1)) ;;
    6) missing_transcript=$((missing_transcript + 1)) ;;
    *) failed=$((failed + 1)) ;;
  esac
  case $source_result in
    0|2|3) ;;
    *) unresolved_sources="$(printf '%s' "$unresolved_sources" | jq --arg id "$doc_id" --argjson result "$source_result" --argjson record "$record" '. + [{document_id:$id, result:$result, record:$record}]')" ;;
  esac
  sleep 0.2
done < "$docs_tsv"
rm -f "$docs_tsv"

# ── State + summary ─────────────────────────────────────────────────────────
now="$NOW_ISO"
if [[ $failed -eq 0 && $stub -eq 0 && $no_access -eq 0 && $missing_transcript -eq 0 ]]; then
  if [[ $DRY_RUN -eq 1 ]]; then
    log "INFO   done (dry-run): pulled=$pulled skipped=$skipped stub=$stub no_access=$no_access missing_transcript=$missing_transcript failed=0 (state file NOT updated)"
  else
    write_state "$(jq -n \
      --arg now "$now" \
      --argjson pulled "$pulled" \
      '{
         last_success_at: $now,
         last_failure_at: null,
         last_failure_reason: null,
         consecutive_failures: 0,
         last_run_at: $now,
         last_run_pulled: $pulled,
         unresolved_sources: []
       }')"
    log "INFO   done: pulled=$pulled skipped=$skipped stub=$stub no_access=$no_access missing_transcript=$missing_transcript failed=0"
  fi
  exit 0
else
  prev_fails="$(read_state_field "consecutive_failures" "0")"
  new_fails=$(( prev_fails + 1 ))
  prev_success="$(read_state_field "last_success_at" "")"
  write_state "$(jq -n \
    --arg now "$now" \
    --argjson pulled "$pulled" \
    --argjson fails "$new_fails" \
    --argjson unresolved "$unresolved_sources" \
    --arg prev_success "$prev_success" \
    '{
       last_success_at: (if $prev_success == "" then null else $prev_success end),
       last_failure_at: $now,
       last_failure_reason: "unresolved-sources",
       consecutive_failures: $fails,
       last_run_at: $now,
       last_run_pulled: $pulled,
       unresolved_sources: $unresolved
     }')"
  log "WARN   partial: pulled=$pulled skipped=$skipped stub=$stub no_access=$no_access missing_transcript=$missing_transcript failed=$failed consec_fails=$new_fails"
  exit 1
fi
