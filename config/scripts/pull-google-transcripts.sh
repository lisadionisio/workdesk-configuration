#!/usr/bin/env bash
# pull-google-transcripts.sh — Daily ETL pull of Google Meet transcripts into
# system/intake/.
#
# Pure ETL. No AI tokens. Idempotent (skips docs already pulled, by
# google-drive-file-id across system/intake/ and system/transcripts/).
#
# Uses the `gws` CLI (Google Workspace) which handles OAuth via local cache.
# Lists Google Docs owned by the operator with names matching the Meet
# transcript convention "<Title> - YYYY/MM/DD HH:MM TZ - Transcript", exports
# each as plain text, and writes a markdown file conforming to the transcript
# source seed.
#
# Cross-source dedupe (against Granola pulls) is intentionally deferred to the
# processing pass — both sources land in intake and `/process-transcripts`
# merges or chooses per operator-confirmed rules.
#
# Usage:
#   bash config/scripts/pull-google-transcripts.sh --account you@example.com                         # default: --days 1
#   bash config/scripts/pull-google-transcripts.sh --account you@example.com --days 7
#   bash config/scripts/pull-google-transcripts.sh --account you@example.com --days 30 --backfill    # >7 requires --backfill
#   bash config/scripts/pull-google-transcripts.sh --account you@example.com --dry-run
#   bash config/scripts/pull-google-transcripts.sh --account you@example.com --file-id <id> --force
#   Add --reviewed-headerless-sha256 <raw-export-sha256> only after source review.
#   bash config/scripts/pull-google-transcripts.sh --account you@example.com --status
#   bash config/scripts/pull-google-transcripts.sh --account you@example.com --help
#
# Exit codes:
#   0  success (may include zero new pulls)
#   1  partial — some docs failed to pull
#   2  hard failure (auth, bad args, API unreachable)

set -uo pipefail

# ── Constants ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INTAKE_DIR="$VAULT_ROOT/system/intake"
TRANSCRIPTS_DIR="$VAULT_ROOT/system/transcripts"
# Progress is host-local and explicitly scoped to vault and Google principal.
# Unattributed legacy checkpoints are never adopted automatically.
umask 077
STATE_FILE=""
READ_STATE_FILE=""
LOG_FILE=""
SOURCE_FORMAT="google-meet-transcript"

# ── Defaults ─────────────────────────────────────────────────────────────────
DAYS=1
BACKFILL=0
DRY_RUN=0
FORCE_FILE_ID=""
FORCE=0
SHOW_STATUS=0
ACCOUNT="${WORKDESK_GWS_ACCOUNT:-}"
HEADERLESS_SHA256=""

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

# Convert ISO UTC timestamp to operator-local YYYY-MM-DD
iso_utc_to_local_date() {
  local utc="$1"
  utc="${utc%%.*}"
  utc="${utc%Z}"
  TZ=UTC date -j -f "%Y-%m-%dT%H:%M:%S" "$utc" "+%s" 2>/dev/null \
    | xargs -I {} date -r {} "+%Y-%m-%d" 2>/dev/null
}

# Meet transcript filename pattern: "<Title> - YYYY/MM/DD HH:MM TZ - Transcript"
# Returns: title|date-iso-ymd  (pipe-separated; date is YYYY-MM-DD)
parse_meet_filename() {
  local fname="$1"
  # Strip trailing " - Transcript"
  local stripped="${fname% - Transcript}"
  # Match " - YYYY/MM/DD HH:MM TZ" at end
  if [[ "$stripped" =~ (.*)\ -\ ([0-9]{4})/([0-9]{1,2})/([0-9]{1,2})\ [0-9]{1,2}:[0-9]{2}\ [A-Z]+$ ]]; then
    local title="${BASH_REMATCH[1]}"
    local y="${BASH_REMATCH[2]}"
    local m="${BASH_REMATCH[3]}"
    local d="${BASH_REMATCH[4]}"
    # Force base-10: bash printf reads leading-zero values (08, 09) as octal and errors.
    printf '%s|%04d-%02d-%02d' "$title" "$((10#$y))" "$((10#$m))" "$((10#$d))"
  else
    # Fallback: no date in filename, use whole stripped as title, today as date
    printf '%s|%s' "$stripped" "$(date "+%Y-%m-%d")"
  fi
}

write_state() {
  local content="$1"
  [[ $DRY_RUN -eq 1 ]] && return 0
  mkdir -p "$(dirname "$STATE_FILE")" || exit 2
  local tmp
  tmp="$(mktemp "$STATE_DIR/.checkpoint-XXXXXXXX")" || exit 2
  printf '%s\n' "$content" | jq --arg account "$ACCOUNT" '. + {account: $account}' > "$tmp" || exit 2
  mv "$tmp" "$STATE_FILE" || exit 2
  READ_STATE_FILE="$STATE_FILE"
}

read_state_field() {
  local field="$1"
  local default="$2"
  if [[ -f "$READ_STATE_FILE" ]]; then
    jq -r --arg field "$field" --arg default "$default" \
      '(.[$field] // $default)' "$READ_STATE_FILE" 2>/dev/null \
      || printf '%s' "$default"
  else
    printf '%s' "$default"
  fi
}

# ── Parse args ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --account)  [[ $# -ge 2 && -n "$2" ]] || { echo "--account requires an email" >&2; exit 2; }; ACCOUNT="$2"; shift 2 ;;
    --days)     [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]*$ ]] || { echo "--days requires a positive integer" >&2; exit 2; }; DAYS="$2"; shift 2 ;;
    --backfill) BACKFILL=1; shift ;;
    --dry-run)  DRY_RUN=1; shift ;;
    --file-id)  [[ $# -ge 2 && "$2" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "--file-id requires a Drive ID" >&2; exit 2; }; FORCE_FILE_ID="$2"; shift 2 ;;
    --force)    FORCE=1; shift ;;
    --reviewed-headerless-sha256)
      [[ $# -ge 2 && "$2" =~ ^[0-9a-f]{64}$ ]] || { echo "A reviewed raw-export SHA-256 is required" >&2; exit 2; }
      HEADERLESS_SHA256="$2"; shift 2 ;;
    --status)   SHOW_STATUS=1; shift ;;
    --help|-h)
      sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'
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
STATE_DIR="${WORKDESK_STATE_HOME:-$HOME/.local/state/workdesk}/$VAULT_KEY/google-transcripts/$ACCOUNT_KEY"
STATE_FILE="$STATE_DIR/pull-google.json"
READ_STATE_FILE="$STATE_FILE"
LOG_FILE="$STATE_DIR/pull-google-transcripts.log"

# Refuse damaged checkpoints rather than resetting history or widening a query.
if [[ -e "$READ_STATE_FILE" || -L "$READ_STATE_FILE" ]]; then
  if [[ ! -f "$READ_STATE_FILE" || -L "$READ_STATE_FILE" ]] || ! jq -e --arg account "$ACCOUNT" '
    type == "object" and .account == $account and
    ((.last_success_at == null) or (.last_success_at | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"))) and
    ((.consecutive_failures == null) or (.consecutive_failures | type == "number" and . >= 0 and floor == .))
  ' "$READ_STATE_FILE" >/dev/null 2>&1; then
    printf 'ERROR: invalid checkpoint; preserved for reconciliation\n' >&2
    exit 2
  fi
  checkpoint_date="$(jq -r '.last_success_at // empty' "$READ_STATE_FILE")"
  if [[ -n "$checkpoint_date" ]] && ! date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "$checkpoint_date" +%s >/dev/null 2>&1; then
    printf 'ERROR: invalid checkpoint date; preserved for reconciliation\n' >&2
    exit 2
  fi
fi

# ── --status mode ───────────────────────────────────────────────────────────
if [[ $SHOW_STATUS -eq 1 ]]; then
  echo "Google Meet transcripts pull status:"
  if [[ -f "$READ_STATE_FILE" ]]; then
    jq -r '
      "  last_success_at:    \(.last_success_at // "never")",
      "  last_failure_at:    \(.last_failure_at // "never")",
      "  consecutive_fails:  \(.consecutive_failures // 0)",
      "  files_last_run:     \(.last_run_pulled // 0)",
      "  last_run_at:        \(.last_run_at // "never")"
    ' "$READ_STATE_FILE"
    last_success="$(jq -r '.last_success_at // empty' "$READ_STATE_FILE")"
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
    fi
  else
    echo "  (no state file — script has never run successfully)"
  fi
  exit 0
fi

# ── Validate args ───────────────────────────────────────────────────────────
if [[ -n "$HEADERLESS_SHA256" && ( -z "$FORCE_FILE_ID" || $FORCE -ne 1 ) ]]; then
  echo "Reviewed headerless recovery requires --file-id and --force" >&2
  exit 2
fi
if [[ -n "$FORCE_FILE_ID" && $FORCE -eq 0 ]]; then
  echo "Error: --file-id requires --force" >&2
  exit 2
fi

if [[ "$DAYS" -gt 7 && $BACKFILL -eq 0 && -z "$FORCE_FILE_ID" ]]; then
  echo "Error: --days $DAYS > 7 requires --backfill" >&2
  exit 2
fi

# Auto-extend lookback if last success was older than --days
last_success_at="$(read_state_field "last_success_at" "")"
if [[ -n "$last_success_at" && -z "$FORCE_FILE_ID" && $BACKFILL -eq 0 ]]; then
  last_epoch="$(TZ=UTC date -j -f "%Y-%m-%dT%H:%M:%SZ" "$last_success_at" "+%s" 2>/dev/null || echo 0)"
  now_epoch="$(date -u "+%s")"
  delta_days=$(( (now_epoch - last_epoch + 86399) / 86400 ))
  if [[ $delta_days -gt $DAYS ]]; then
    log "INFO   last success was $delta_days days ago; extending lookback from $DAYS to $delta_days"
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
       consecutive_failures: $fails,
       last_run_at: $last_failure,
       last_run_pulled: 0
     }')"
  exit 2
fi

# ── Cutoff ──────────────────────────────────────────────────────────────────
RUN_STARTED_EPOCH="$(date -u "+%s")"
RUN_STARTED_AT="$(date -u -r "$RUN_STARTED_EPOCH" "+%Y-%m-%dT%H:%M:%SZ")"
CUTOFF_EPOCH=$(( RUN_STARTED_EPOCH - DAYS * 86400 ))
# Drive uses a strict greater-than boundary. Overlap the last checkpoint by
# one second, independent of time spent authenticating and rounding days.
if [[ -n "$last_success_at" && $BACKFILL -eq 0 && -z "$FORCE_FILE_ID" ]]; then
  if [[ $CUTOFF_EPOCH -ge $last_epoch ]]; then CUTOFF_EPOCH=$(( last_epoch - 1 )); fi
fi
CUTOFF_ISO="$(date -u -r "$CUTOFF_EPOCH" "+%Y-%m-%dT%H:%M:%SZ")"

log "INFO   starting Google Meet pull (days=$DAYS dry_run=$DRY_RUN force_file=${FORCE_FILE_ID:-none} cutoff=$CUTOFF_ISO)"

# ── Write intake file for one Doc ───────────────────────────────────────────
# Args: <file_id> <name> <created_time>
write_intake_for_doc() {
  local file_id="$1"
  local fname="$2"
  local created_at="$3"

  # Parse filename → title, date
  local parsed title local_date
  parsed="$(parse_meet_filename "$fname")"
  title="${parsed%|*}"
  local_date="${parsed##*|}"

  local slug filename target_path
  slug="$(slug_from_title "$title")"
  [[ -z "$slug" ]] && slug="$(printf 'untitled-%s' "${file_id:0:8}" | tr 'A-Z' 'a-z')"
  filename="${local_date}-${slug}.md"
  target_path="$INTAKE_DIR/$filename"

  # Collision handling
  if [[ -e "$target_path" ]]; then
    if grep -q "^google-drive-file-id: ${file_id}[[:space:]]*\$" "$target_path" 2>/dev/null; then
      if [[ $FORCE -eq 1 && "$FORCE_FILE_ID" == "$file_id" ]]; then
        : # stage refresh; publication preserves existing note for reconciliation
      else
        log "SKIP   $file_id already-pulled (in intake) → $filename"
        return 2
      fi
    else
      local short_suffix="${file_id:0:6}"
      filename="${local_date}-${slug}-${short_suffix}.md"
      target_path="$INTAKE_DIR/$filename"
      log "INFO   filename collision avoided via suffix → $filename"
    fi
  fi

  # Idempotency vs archive
  if [[ $FORCE -eq 0 || "$FORCE_FILE_ID" != "$file_id" ]]; then
    if grep --include='*.md' -rlq "^google-drive-file-id: ${file_id}[[:space:]]*\$" "$TRANSCRIPTS_DIR" 2>/dev/null; then
      log "SKIP   $file_id already-processed (in transcripts/) → $filename"
      return 2
    fi
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    log "DRY    $file_id WOULD pull → $filename (\"$title\" $local_date)"
    return 3
  fi

  # Export the doc as text to a temp file.
  # gws (>=0.22.5) rejects --output paths that resolve outside the current
  # working directory, so export from inside a dedicated temp dir using a
  # relative filename. Robust regardless of the caller's cwd.
  local body_dir; body_dir="$(mktemp -d)"
  local body_tmp="$body_dir/export.txt"
  if ! ( cd "$body_dir" && gws drive files export \
        --params "$(jq -n --arg id "$file_id" '{fileId: $id, mimeType: "text/plain"}')" \
        --output "export.txt" >/dev/null 2>&1 ); then
    log "ERROR  $file_id export failed"
    rm -f "$body_tmp"; rmdir "$body_dir"
    return 4
  fi

  # A headerless recovery is bound to the exact raw export reviewed by the
  # operator/agent. It is never an automatic fallback for arbitrary documents.
  if [[ -n "$HEADERLESS_SHA256" && "$(shasum -a 256 "$body_tmp" | cut -d ' ' -f 1)" != "$HEADERLESS_SHA256" ]]; then
    log "ERROR  $file_id reviewed export changed; source not published"
    rm -f "$body_tmp"; rmdir "$body_dir"
    return 4
  fi

  # Strip BOM if present
  if head -c 3 "$body_tmp" | od -An -c | grep -q '357 273 277'; then
    tail -c +4 "$body_tmp" > "$body_tmp.unbomb"
    mv "$body_tmp.unbomb" "$body_tmp"
  fi

  # Normalize CRLF → LF. Drive text/plain exports use CRLF; leaving it in
  # produces mixed-ending files that editors later normalize to all-CRLF,
  # which breaks every $-anchored frontmatter grep (dedupe, scans).
  tr -d '\r' < "$body_tmp" > "$body_tmp.lf"
  mv "$body_tmp.lf" "$body_tmp"

  # Publish attendee metadata only from a complete, bounded header section.
  # A reviewed headerless export is raw evidence, never an attendee list.
  local attendees_yaml="" attendees_status="unverified"
  if [[ -z "$HEADERLESS_SHA256" ]]; then
    attendees_yaml="$(awk '
      /^Transcript[[:space:]]*$/ { complete=1; exit }
      /^Attendees[[:space:]]*$/ { if (seen) invalid=1; seen=1; next }
      seen {
        bytes += length($0); lines++
        if (bytes > 4096 || lines > 32 || $0 ~ /:/) invalid=1
        if (invalid) next
        count=split($0, fields, ",")
        for (i=1; i<=count; i++) {
          name=fields[i]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
          if (name != "") {
            if (n >= 32 || length(name) > 120) { invalid=1; break }
            names[++n]=name
          }
        }
      }
      END { if (seen && complete && !invalid) for (i=1; i<=n; i++) print names[i] }
    ' "$body_tmp" | jq -Rr '"  - " + tojson')"
    [[ -n "$attendees_yaml" ]] && attendees_status="header-list"
  else
    attendees_status="not-extracted-headerless"
  fi
  [[ -z "$attendees_yaml" ]] && attendees_yaml='  []'

  # Extract body — everything after the "Transcript" line
  local transcript_body
  transcript_body="$(awk 'f{print} /^Transcript[[:space:]]*$/{f=1}' "$body_tmp")"
  if [[ -n "$HEADERLESS_SHA256" ]]; then
    if grep -qE '^Transcript[[:space:]]*$' "$body_tmp"; then
      log "ERROR  $file_id has a transcript heading; reviewed headerless recovery does not apply"
      rm -f "$body_tmp"; rmdir "$body_dir"
      return 4
    fi
    transcript_body="$(cat "$body_tmp")"
  fi
  # Bash 3.2 pattern replacement is prohibitively slow on long transcripts.
  # Consume the full stream (no grep -q) so pipefail cannot mistake SIGPIPE
  # from an early reader exit for an empty body.
  if ! printf '%s' "$transcript_body" | LC_ALL=C grep '[^[:space:]]' >/dev/null; then
    log "ERROR  $file_id export has no recognized transcript body; source not published"
    rm -f "$body_tmp"; rmdir "$body_dir"
    return 4
  fi

  # Drive web URL
  local drive_url="https://docs.google.com/document/d/$file_id"

  local pulled_at
  pulled_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  local tmp
  tmp="$(mktemp "$INTAKE_DIR/.pull-google-XXXXXXXX")" || return 4
  {
    printf -- '---\n'
    printf -- 'type: source\n'
    printf -- 'source-kind: transcript\n'
    printf -- 'date: %s\n' "$local_date"
    printf -- 'processed: false\n'
    printf -- 'processed-into: []\n'
    printf -- 'title: %s\n' "$(printf '%s' "$title" | jq -Rs '.')"
    printf -- 'google-drive-file-id: %s\n' "$file_id"
    printf -- 'google-drive-url: %s\n' "$drive_url"
    printf -- 'google-created-at: %s\n' "$created_at"
    printf -- 'attendees-from-source:\n'
    printf -- '%s\n' "$attendees_yaml"
    printf -- 'attendee-extraction-status: %s\n' "$attendees_status"
    printf -- 'source-format: %s\n' "$SOURCE_FORMAT"
    printf -- 'pulled-at: %s\n' "$pulled_at"
    printf -- '---\n\n'
    printf -- '# %s — %s (raw transcript)\n\n' "$title" "$local_date"
    if [[ -n "$HEADERLESS_SHA256" ]]; then
      printf -- 'Source layout: headerless export reviewed for this document. Raw export SHA-256: `%s`. The full export is preserved below after line-ending normalization; no attendance was inferred from speaker names.\n\n' "$HEADERLESS_SHA256"
    fi
    printf -- 'Verbatim Google Meet transcript. Speakers are name-resolved by Google (e.g., "Martin Holland: …") so the processing pass can map directly to `atlas/people/` per [[../../config/objects/meeting]] step 3 without diarization-label resolution. When `attendee-extraction-status` is `header-list`, `attendees-from-source` records the bounded list embedded by Google, not verified attendance. Otherwise extraction is unverified or unavailable; an empty list does not establish that nobody attended. Cross-reference against speaker turns during processing.\n\n'
    printf -- '## Transcript\n\n'
    printf -- '%s\n' "$transcript_body"
  } > "$tmp" || { log "ERROR  $file_id staging write failed; candidate retained: $tmp"; return 4; }

  # Publish without replacing a file that arrived after the initial lookup.
  # Hard-linking on the same filesystem makes destination existence atomic.
  # A forced refresh also requires explicit reconciliation of an existing note.
  if ! python3 - "$tmp" "$target_path" <<'PYPUBLISH'
import os, sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
try:
    with source.open('rb') as handle:
        os.fsync(handle.fileno())
    os.link(source, target)
except OSError:
    print('Intake publication refused; existing destination and staged source preserved.', file=sys.stderr)
    sys.exit(1)
source.unlink()
PYPUBLISH
  then
    log "ERROR  $file_id publication failed; reconcile staged candidate: $tmp"
    rm -f "$body_tmp"; rmdir "$body_dir"
    return 4
  fi
  rm -f "$body_tmp"; rmdir "$body_dir"

  local size; size="$(wc -c < "$target_path" | tr -d ' ')"
  log "PULL   $file_id → $filename (${size}b) \"$title\""
  return 0
}

# ── Forced single-file path ─────────────────────────────────────────────────
if [[ -n "$FORCE_FILE_ID" ]]; then
  meta="$(gws drive files get --params \
    "$(jq -n --arg id "$FORCE_FILE_ID" \
       '{fileId: $id, fields: "id,name,createdTime,mimeType"}')" \
    --format json 2>/dev/null)"
  if ! printf '%s' "$meta" | jq -e --arg id "$FORCE_FILE_ID" '
    type == "object" and (has("error") | not) and .id == $id and
    (.name | type == "string" and (test("[\\t\\r\\n]") | not)) and
    (.createdTime | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z$"))
  ' >/dev/null 2>&1; then
    log "ERROR  $FORCE_FILE_ID metadata fetch failed"
    exit 2
  fi
  fname="$(printf '%s' "$meta" | jq -r '.name')"
  created="$(printf '%s' "$meta" | jq -r '.createdTime')"
  write_intake_for_doc "$FORCE_FILE_ID" "$fname" "$created"
  rc=$?
  case "$rc" in
    0|2|3) exit 0 ;;
    *) exit 1 ;;
  esac
fi

# ── List + iterate ──────────────────────────────────────────────────────────
QUERY="mimeType=\"application/vnd.google-apps.document\" and name contains \"- Transcript\" and \"me\" in owners and createdTime > \"$CUTOFF_ISO\""

PARAMS_JSON="$(jq -n --arg q "$QUERY" '{q: $q, fields: "nextPageToken,incompleteSearch,files(id,name,createdTime)", orderBy: "createdTime desc", pageSize: 100}')"

list_json="$(mktemp)"
page_json="$(mktemp)"
merged_json="$(mktemp)"
trap 'rm -f "$list_json" "$page_json" "$merged_json"' EXIT

# Collect and validate every page before exporting any source. A failed later
# page must not be mistaken for a complete enumeration or advance success.
list_all_pages() {
  local params="$PARAMS_JSON" token previous
  local tokens=("")  # Bash 3.2 + nounset cannot expand an empty array.
  printf '{"files":[]}\n' > "$list_json"
  while true; do
    gws drive files list --params "$params" --format json > "$page_json" 2>/dev/null || return 1
    jq -e 'type == "object" and (has("error") | not) and
      ((has("incompleteSearch") | not) or .incompleteSearch == false) and
      (.files | type == "array") and
      all(.files[]; type == "object" and (.id | type == "string" and test("^[A-Za-z0-9_-]+$")) and
        (.name | type == "string" and (test("[\\t\\r\\n]") | not)) and
        (.createdTime | type == "string" and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z$"))) and
      ((has("nextPageToken") | not) or (.nextPageToken | type == "string"))' "$page_json" >/dev/null 2>&1 || return 1
    jq -s '{files: (.[0].files + .[1].files)}' "$list_json" "$page_json" > "$merged_json" || return 1
    cat "$merged_json" > "$list_json" || return 1
    token="$(jq -r '.nextPageToken // ""' "$page_json")" || return 1
    [[ -z "$token" ]] && return 0
    for previous in "${tokens[@]}"; do
      [[ "$previous" != "$token" ]] || return 1
    done
    tokens+=("$token")
    params="$(jq --arg token "$token" '. + {pageToken: $token}' <<< "$PARAMS_JSON")" || return 1
  done
}

if ! list_all_pages; then
  log "ERROR  gws drive files list failed"
  prev_fails="$(read_state_field "consecutive_failures" "0")"
  new_fails=$(( prev_fails + 1 ))
  write_state "$(jq -n \
    --arg now "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
    --argjson fails "$new_fails" \
    --arg prev_success "$last_success_at" \
    '{
       last_success_at: (if $prev_success == "" then null else $prev_success end),
       last_failure_at: $now,
       consecutive_failures: $fails,
       last_run_at: $now,
       last_run_pulled: 0
     }')"
  exit 2
fi

total_found="$(jq '.files | length' "$list_json")"
log "INFO   found $total_found owned-by-me Meet transcripts since $CUTOFF_ISO"

pulled=0
skipped=0
failed=0

while IFS=$'\t' read -r file_id fname created_at; do
  [[ -z "$file_id" ]] && continue

  # Pre-fetch idempotency: skip without exporting if file_id is already on disk
  # in intake/ or transcripts/. Handles suffix-renamed collisions that the
  # in-function check below would miss.
  if [[ $FORCE -eq 0 ]] && grep --include='*.md' -rlq "^google-drive-file-id: ${file_id}[[:space:]]*\$" "$INTAKE_DIR" "$TRANSCRIPTS_DIR" 2>/dev/null; then
    log "SKIP   $file_id already-pulled (pre-fetch)"
    skipped=$((skipped + 1))
    continue
  fi

  write_intake_for_doc "$file_id" "$fname" "$created_at"
  case $? in
    0) pulled=$((pulled + 1)) ;;
    2) skipped=$((skipped + 1)) ;;
    3) pulled=$((pulled + 1)) ;;
    *) failed=$((failed + 1)) ;;
  esac
  sleep 0.2
done < <(jq -r '.files[] | "\(.id)\t\(.name)\t\(.createdTime)"' "$list_json")

# ── State + summary ─────────────────────────────────────────────────────────
now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
if [[ $failed -eq 0 ]]; then
  if [[ $DRY_RUN -eq 1 ]]; then
    log "INFO   done (dry-run): pulled=$pulled skipped=$skipped failed=0 (state file NOT updated)"
  else
    write_state "$(jq -n \
      --arg now "$now" \
      --arg completed_through "$RUN_STARTED_AT" \
      --argjson pulled "$pulled" \
      '{
         last_success_at: $completed_through,
         last_failure_at: null,
         consecutive_failures: 0,
         last_run_at: $now,
         last_run_pulled: $pulled
       }')"
    log "INFO   done: pulled=$pulled skipped=$skipped failed=0"
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
    --arg prev_success "$prev_success" \
    '{
       last_success_at: (if $prev_success == "" then null else $prev_success end),
       last_failure_at: $now,
       consecutive_failures: $fails,
       last_run_at: $now,
       last_run_pulled: $pulled
     }')"
  log "WARN   partial: pulled=$pulled skipped=$skipped failed=$failed consec_fails=$new_fails"
  exit 1
fi
