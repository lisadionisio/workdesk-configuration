---
name: get-transcripts
description: Pull raw verbatim transcripts from Granola (meetings, notes, phone calls), Google Meet standalone Transcript Docs, AND the verbatim Transcript tab inside "Notes by Gemini" Docs — into system/intake/ via three ETL scripts. Pure script orchestration — no AI synthesis, no tokens spent on extraction. Idempotent (skips already-pulled). Default lookback 7 days; flags for 14d / backfill / dry-run / status / single-source. Use when the operator says "get transcripts", "pull transcripts", "grab the last N days of transcripts", or before /process-transcripts.
---

# /get-transcripts

Pull the last N days of verbatim transcripts into `system/intake/`. Wraps three pure-ETL scripts — no LLM tokens spent on extraction — so what lands in intake is the raw source, not a summary.

## Invocation

- `/get-transcripts` — default 7-day lookback, all three sources
- `/get-transcripts --days 14` — 2-week lookback (auto-applies `--backfill` since >7)
- `/get-transcripts --days 30 --backfill` — 30-day backfill (explicit)
- `/get-transcripts --source granola` — Granola only
- `/get-transcripts --source google` — Google Meet standalone Transcript Docs only
- `/get-transcripts --source google --account you@example.com` — explicit Google account
- `/get-transcripts --source gemini` — "Notes by Gemini" Docs only (Transcript tab)
- `/get-transcripts --dry-run` — list what would pull, no writes
- `/get-transcripts --status` — show state files only, no pull

## What it does

Runs the selected ETL scripts sequentially. They share the intake directory;
separate checkpoint files alone do not prove concurrent publication is safe.

| Source | Script | Captures | Source-id field |
|---|---|---|---|
| Granola (meetings, phone calls, in-person notes) | `config/scripts/pull-granola.sh` | Verbatim transcript via `/v1/notes/{id}?include=transcript`. Diarization labels (Speaker A, B, …) — speaker resolution happens at processing time. | `granola-note-id` |
| Google Meet standalone Transcript Docs (Drive Docs named `"…- Transcript"`) | `config/scripts/pull-google-transcripts.sh` | Single-tab Doc, exported as plain text via Drive export. Speakers are name-resolved by Google. | `google-drive-file-id` |
| "Notes by Gemini" Docs (Transcript tab only) | `config/scripts/pull-gemini-transcripts.sh` | Tab-2 verbatim transcript via Docs API with `includeTabsContent: true`. Speakers are name-resolved by Google. Source enumeration goes through Calendar (the `Notes by Gemini` attachment), not Drive search — the Docs don't have a stable name pattern. | `gemini-doc-id` |

**All three scripts write to `system/intake/`** with `source-kind: transcript` frontmatter and `processed: false`. All three are idempotent: re-running skips anything already on disk (in intake or in the `system/transcripts/` archive) by source id.

The Google and Gemini scripts target different document layouts. Successful
runs establish coverage only for the accounts, queries, date windows and
accessible documents actually checked. Report unavailable sources, missing
access and skipped transcript tabs; never claim every meeting was captured.

## Phases

### 1. Show status first (always)

Resolve the requested Google accounts from the operator's request or an
explicit existing job configuration before running Google status or pulls.
Do not infer an account from the shell's current login or assume all configured
accounts belong in this request. Pass `--account` separately for each selected
account to both Google-backed importers, including `--status`. See
`config/rules/tools/gws.md` for host-local routes and checkpoint migration.
Confirm the installed script supports `--account`. If an older installation
does not, report the version mismatch and update it before pulling; do not
fall back to an unqualified Google or Gemini run.

Run all selected scripts with `--status` and surface the result. The operator should see:

- Last successful pull (timestamp + hours ago)
- Consecutive failures (if any)
- HEALTH: ok / STALE / INCOMPLETE / UNKNOWN (as supported by the selected importer)

If any source shows `HEALTH: STALE` (>36h since last success) or `HEALTH: INCOMPLETE`, `HEALTH: UNKNOWN`, or `consecutive_fails ≥ 1`, call that out — auth or API issues need fixing before the pull will succeed.

### 2. Decide flags

- `--days N`:
  - No arg → 7
  - `N ≤ 7` → pass through as-is
  - `N > 7` → pass with `--backfill` (the scripts hard-fail on >7 without backfill, as a guard rail)
- `--source`:
  - No arg → run all three
  - `granola` → only `pull-granola.sh`
  - `google` → only `pull-google-transcripts.sh`
  - `gemini` → only `pull-gemini-transcripts.sh`
- `--dry-run` and `--status` pass through.
- Google and Gemini `--account` pass through to both status and pull. Multiple approved
  accounts run sequentially, each with its own checkpoint and reported result.

### 3. Run pulls

Before pulling, check the selected jobs' current owners and running processes.
Do not race a scheduled writer or another manual pull. A timeout alone does not
prove a writer stopped. Run the selected sources and Google accounts sequentially.
Google and Gemini progress are host-local and account-scoped; an unattributed legacy
checkpoint is not a valid automatic starting point for another account.

Capture the tail of each log:

```
INFO   done: pulled=N skipped=N failed=N
```

If `failed > 0`, surface the error lines (`grep ERROR` on the log) — don't bury them. The exit code distinguishes:
- `0` — success (may be zero pulls)
- `1` — partial (some files failed; state file updated with `consecutive_failures` increment)
- `2` — hard failure (auth, bad args, API unreachable)

### 4. Summarize

Report concisely to the operator:

```
Granola:      pulled=N skipped=N failed=N
Google Meet (account): pulled=N skipped=N failed=N
Gemini Docs:  pulled=N skipped=N stub=N no_access=N failed=N
Now in system/intake/ (transcripts only): N

Next: /process-transcripts to extract into atlas/meetings, decisions, people.
```

Gemini-specific counts to expose:
- **stub** — Extracted text is below the importer's minimum length. It needs review; length alone does not establish transcription failure or absence of useful content.
- **no_access** — The document request returned 403/404. Access or availability needs review; the code alone does not establish who owns the document or why it is unavailable.

Either count makes Gemini coverage incomplete (exit 1) and preserves the last
successful enumeration checkpoint. Its `unresolved_sources` queue retains IDs,
result codes and the original calendar context. Later enumeration retries those
documents even if the attachment disappears. A verified existing or newly
published source clears its entry; absence from a query does not. Authentication
and pagination failures preserve the queue. An initial authentication failure
records a coverage-start anchor without claiming success. Explicit dispositions
and concurrent-run verification remain separate gates; do not delete entries
just to make a run appear healthy.

If anything failed (true `failed > 0`), point at the log files:
- `system/cron-pull-granola.log`
- Google: the selected account's `pull-google-transcripts.log` beside its
  host-local checkpoint, as documented in `config/rules/tools/gws.md`
- Gemini: the selected account's `pull-gemini-transcripts.log` beside its
  host-local checkpoint, as documented in `config/rules/tools/gws.md`

### 5. Verify (when ≥1 new file pulled)

For Gemini sources, `calendar-invitees` preserves the Calendar objects without
establishing attendance. The importer leaves `attendees-from-source: []` until
participation can be determined from transcript evidence. Do not interpret that
empty field as proof of an unattended meeting.

A document-only Gemini pull may produce an `undated-` source with `date: null`.
That preserves available raw text without inventing a meeting date. Report the
missing calendar context; retrieval time is not a substitute occurrence date.

Spot-check that frontmatter is well-formed on one new file from each source. Specifically:
- `source-kind: transcript`
- `processed: false`
- `source-format: granola-public-api`, `google-meet-transcript`, or `gemini-meet-transcript`
- A transcript body present under `## Transcript`

Do NOT process — that's `/process-transcripts`'s job.

### 6. Cross-source overlap

A single human meeting may produce intake files from multiple sources (e.g., Granola was recording AND a Google Meet transcript was generated AND Gemini Notes was on). This is **by design** — cross-source dedupe happens at `/process-transcripts` time, where the operator picks the strongest source per meeting (Granola for phone calls and diarized speakers; Google/Gemini for name-resolved speakers; whichever has the cleanest text). At pull time, all three land in intake with distinct source-ids.

If the operator asks "did I get duplicates", run:
```bash
grep -l '^date: <YYYY-MM-DD>$' system/intake/*.md | xargs grep -l '^source-kind: transcript$'
```
and look for same-day files with overlapping titles.

## Failure modes and recovery

| Symptom | Likely cause | Fix |
|---|---|---|
| `ERROR  could not read PERSONAL_GRANOLA_API_KEY from Infisical` | Infisical session expired | `infisical login`, then re-run |
| `ERROR  gws auth failed` | gws token expired | `gws auth login --account you@example.com`, then `bash config/scripts/gws-push-tokens-to-infisical.sh` per [[config/rules/tools/gws|../../config/rules/tools/gws]] |
| All scripts hard-fail (exit 2) | gws auth state missing (`~/Library/Application Support/gws` pre-0.22, `~/.config/gws` on gws 0.22+) | Run `bash config/scripts/setup-gws.sh` |
| `consecutive_fails > 3` | Auth has been broken for multiple cron runs | Always check `--status` first; run remediation above |
| Gemini script reports `no_access > 0` | A requested document was unavailable (403/404); cause unverified | Report incomplete coverage and retain its source ID. Verify the selected account and document access before proposing recovery. |
| Gemini script reports `stub > 0` | Extracted text fell below the importer threshold | Review the actual document and extraction layout; do not assume a recording exists or transcription failed. |
| Gemini script reports `missing_transcript > 0` | The returned document has no Transcript tab | Keep its source ID and calendar context for retry. Report a coverage gap; Notes/Full notes are not substitutes. If transcription appears later, replay can import it. |

For a legitimate short Gemini transcript, inspect the full extracted display text
and record its SHA-256 before recovery. Use `--doc-id ID --force
--reviewed-short-sha256 SHA256` with the selected account. The checksum covers the
UTF-8 output of `config/scripts/lib/gemini_document_text.py`, including dates and
newlines, not the Docs JSON response. Never calculate a checksum merely to bypass
an unresolved review. A mismatch blocks publication even if the new content is
longer than the threshold. The note retains the reviewed checksum; this single-doc
operation does not advance calendar coverage or authorize replacing an old source.
Missing Transcript tabs remain gaps; do not substitute Notes or Full notes.

## What NOT to do

- **Don't synthesize transcripts during this skill.** Synthesis is `/process-transcripts`'s job — see [[config/rules/source-processing-pattern|../../config/rules/source-processing-pattern]] ("Don't synthesize from an upstream summary when the verbatim source is available"). `/get-transcripts` ends when the raw transcript is on disk in `system/intake/`.
- **Don't pull from Granola's summary endpoint, Gemini's Notes tab (tab 1), or any AI-generated summary.** The scripts pull verbatim only — Granola via `?include=transcript`, Google standalone via the Doc named "<Title> - … - Transcript", Gemini via the Transcript tab (tab 2) of the Notes-by-Gemini Doc. The Notes tab is explicitly skipped per [[config/rules/source-processing-pattern|../../config/rules/source-processing-pattern]].
- **Don't change the scripts' default behavior without a corresponding rule update.** The scripts also run via cron (daily); divergence between the manual and cron paths breaks the audit log in `config/state/pull-*.json`.
- **Don't move pulled files out of `system/intake/`** in this skill. The intake → process → archive flow is enforced by [[config/rules/source-processing-pattern|../../config/rules/source-processing-pattern]]; only `/process-transcripts` moves files to `system/transcripts/`.
- **Don't run with `--days > 14` casually.** The cron runs daily; if state shows last success <2 days ago, `--days 7` (default) is plenty. Wider lookback is for catching up after auth outages.
- **Don't claim complete coverage if a pull failed or Gemini reported `stub > 0`, `missing_transcript > 0`, or `no_access > 0`.** Report the unresolved source IDs and scope. Zero technical failures is not proof that every discovered transcript was captured.
- **Don't dedupe across sources at pull time.** Same human meeting captured by multiple sources is by design — `/process-transcripts` is where the operator picks the strongest verbatim per meeting.

## Cron coexistence

The pull scripts may also run via daily cron. Running this skill manually does NOT conflict — the scripts use atomic writes (`mv tmp → final`) and per-file-id idempotency. Two simultaneous runs would each pull, the second would find the first's files via grep and skip. The only race is the state-file write, which is also atomic via `mv`.

Note: as of 2026-05-28, the Granola cron entry has been removed (cron environment lacks an Infisical session, causing repeated auth failures). Run Granola pulls manually via this skill, which inherits the interactive Infisical session. The Google and Keep.md crons remain — they auth through the Infisical Agent + RAM disk pattern that works in cron.

## Source

- Operator instruction 2026-05-28 — "make this a skill you can reliably run to get-transcripts" (session creating this skill).
- Operator correction 2026-05-28 (same session) — "Notes by Gemini docs have two tabs; tab 2 is the full raw transcript." Added the third script (`pull-gemini-transcripts.sh`) to capture this previously-missed source.
- Underlying scripts: `config/scripts/pull-granola.sh`, `config/scripts/pull-google-transcripts.sh`, `config/scripts/pull-gemini-transcripts.sh`.
- Source seed: `config/sources/transcript.md`.
- Related: `process-transcripts` (the next step after pulling).
