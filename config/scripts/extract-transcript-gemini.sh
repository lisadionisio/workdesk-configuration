#!/usr/bin/env bash
# extract-transcript-gemini.sh — Pure ETL: send one intake transcript MD to
# Gemini 3.1 Flash Lite with a strict JSON schema, return structured output.
#
# This is the extraction step of /process-transcripts.  Heavy compute lives
# here (Gemini, ~$0.001-0.002 per transcript) so Claude can stay focused on
# vault integration: wikilink resolution, matching cross-updates, file ops.
#
# Pure ETL discipline:
#   - One transcript in, one JSON out (stdout).
#   - No vault writes.  No state files.  Caller owns those.
#   - Errors go to stderr + non-zero exit.
#
# Usage:
#   bash config/scripts/extract-transcript-gemini.sh <transcript-path> [--model <id>]
#
# Exit codes:
#   0  success — complete, structurally validated extraction JSON on stdout
#   1  Gemini API returned an error (rate limit, malformed request, etc.)
#   2  hard failure (auth, bad args, transcript not readable)
#   3  incomplete provider response or invalid extraction JSON contract

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config/scripts/lib/resolve-secret.sh
source "$SCRIPT_DIR/lib/resolve-secret.sh"
VAULT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SKILL_DIR="$VAULT_ROOT/config/skills/process-transcripts"
PROMPT_FILE="$SKILL_DIR/prompt.txt"
SCHEMA_FILE="$SKILL_DIR/schema.json"
VALIDATOR="$SCRIPT_DIR/validate-extraction.py"
MODEL="gemini-3.1-flash-lite"
TRANSCRIPT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)
      if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "ERROR: --model requires an ID" >&2; exit 2
      fi
      MODEL="$2"; shift 2 ;;
    --help|-h)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) TRANSCRIPT="$1"; shift ;;
  esac
done

if [[ -z "$TRANSCRIPT" || ! -r "$TRANSCRIPT" ]]; then
  echo "ERROR: transcript not readable: $TRANSCRIPT" >&2
  exit 2
fi
if [[ ! -r "$PROMPT_FILE" ]]; then
  echo "ERROR: prompt missing: $PROMPT_FILE" >&2
  exit 2
fi
if [[ ! -r "$SCHEMA_FILE" ]]; then
  echo "ERROR: schema missing: $SCHEMA_FILE" >&2
  exit 2
fi

SCHEMA_BODY="$(cat "$SCHEMA_FILE")"
if ! command -v python3 >/dev/null || [[ ! -r "$VALIDATOR" ]] || \
   ! python3 "$VALIDATOR" --schema-json "$SCHEMA_BODY" --check-schema; then
  echo "ERROR: extraction validator or schema unavailable/unsupported" >&2
  exit 2
fi

GEMINI_API_KEY="$(wd_resolve_secret PERSONAL_GEMINI_API_KEY 2>/dev/null)" || true
# Key shape check: classic Google API keys start "AIza"; keys minted since
# mid-2026 start "AQ." — accept both (field-reported 2026-08-03: the old
# AIzaSy-only check rejected every newly issued key).
if [[ -z "$GEMINI_API_KEY" || ( "${GEMINI_API_KEY:0:4}" != "AIza" && "${GEMINI_API_KEY:0:3}" != "AQ." ) ]]; then
  echo "ERROR: No Gemini API key available. Either export PERSONAL_GEMINI_API_KEY, or configure Infisical (bash config/scripts/bootstrap-infisical.sh) and store the key there." >&2
  exit 2
fi

# Read prompt + schema + transcript body (skip frontmatter)
PROMPT_BODY="$(cat "$PROMPT_FILE")"
operator_name="$(
  OPERATOR_CONFIG_LENIENT=1
  if source "$SCRIPT_DIR/lib/operator-config.sh" 2>/dev/null; then
    printf '%s' "${OPERATOR_NAME:-}"
  fi
)"
if [[ -n "$operator_name" ]]; then
  PROMPT_BODY+=$'\n'"The operator's name is: ${operator_name}."
fi
if ! TRANSCRIPT_BODY="$(awk '
  NR == 1 { if ($0 !~ /^---\r?$/) exit 2; next }
  !body { if ($0 ~ /^---\r?$/) body=1; next }
  { print }
  END { if (!body) exit 2 }
' "$TRANSCRIPT")"; then
  echo "ERROR: transcript requires complete leading frontmatter" >&2
  exit 2
fi

if [[ -z "$TRANSCRIPT_BODY" ]]; then
  echo "ERROR: transcript body is empty (no content after frontmatter)" >&2
  exit 2
fi

# Build request
if ! REQUEST=$(jq -n \
  --arg prompt "$PROMPT_BODY" \
  --arg transcript "$TRANSCRIPT_BODY" \
  --argjson schema "$SCHEMA_BODY" \
  '{
    contents: [{
      parts: [{ text: ($prompt + "\n\n" + $transcript) }]
    }],
    generationConfig: {
      responseMimeType: "application/json",
      responseSchema: $schema,
      temperature: 0.2,
      maxOutputTokens: 8192
    }
  }'); then
  echo "ERROR: unable to build extraction request" >&2
  exit 2
fi

# Call Gemini
if ! RESPONSE_FILE="$(mktemp)"; then
  echo "ERROR: unable to allocate provider response file" >&2
  exit 2
fi
trap 'rm -f "$RESPONSE_FILE"' EXIT

if ! HTTP_CODE=$(curl -sS --connect-timeout 15 --max-time 120 -o "$RESPONSE_FILE" -w '%{http_code}' \
  "https://generativelanguage.googleapis.com/v1beta/models/$MODEL:generateContent?key=$GEMINI_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "$REQUEST"); then
  echo "ERROR: Gemini transport failed" >&2
  exit 1
fi

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "ERROR: Gemini HTTP $HTTP_CODE" >&2
  jq '.error // .' "$RESPONSE_FILE" >&2 2>/dev/null || cat "$RESPONSE_FILE" >&2
  exit 1
fi

# Check API-level error in body
if jq -e '.error' "$RESPONSE_FILE" >/dev/null 2>&1; then
  echo "ERROR: Gemini API error" >&2
  jq '.error' "$RESPONSE_FILE" >&2
  exit 1
fi

# Validate the provider completion and the bundled schema. This does not prove
# factual accuracy. Use the same captured schema that was sent in the request.
if ! EXTRACTED="$(python3 "$VALIDATOR" --schema-json "$SCHEMA_BODY" < "$RESPONSE_FILE")"; then
  exit 3
fi

# Emit token usage to stderr so caller can log it without polluting JSON stdout
jq -c '(.usageMetadata // {}) as $usage | {
  prompt: $usage.promptTokenCount, output: $usage.candidatesTokenCount,
  total: $usage.totalTokenCount, modelVersion: .modelVersion, responseId: .responseId
}' "$RESPONSE_FILE" >&2

# JSON to stdout
echo "$EXTRACTED"
