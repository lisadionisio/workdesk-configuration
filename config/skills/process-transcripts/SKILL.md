---
name: process-transcripts
description: Process verbatim transcripts into sourced meeting notes, entity updates and ownership-aware action proposals. Uses Gemini extraction followed by agent-led factual review, vault integration and verified completion. Interactive processing requires operator confirmation; batch mode must be explicitly selected.
---

# /process-transcripts

Move raw transcripts through the extraction pipeline into structured vault notes. Confirm each transcript in interactive mode; an explicit `--all` request authorizes the selected batch. Unattended processing requires separate workflow acceptance and authorization.

## Architecture

Gemini produces structured extraction. The active integration agent reviews facts, resolves vault references, applies sourced updates and verifies completion using the same files and rules across supported runtimes.

```
intake/{slug}.md (verbatim)
        │
        ▼  config/scripts/extract-transcript-gemini.sh
Gemini 3.1 Flash Lite (configured extractor)
        │  strict JSON schema (config/skills/process-transcripts/schema.json)
        │
        ▼  Active integration agent (record actual runtime/model)
   • Validate JSON
   • Resolve names → wikilinks (Glob over atlas/people/)
   • Network-scope filter for person notes
   • Write atlas/meetings/{slug}.md
   • Apply matching cross-updates
   • Route action items by owner_category
   • Review facts and validate output properties/links
   • Complete intake → transcripts/ through the verified helper
```

The Gemini prompt + JSON schema live next to this file:
- [`prompt.txt`](prompt.txt)
- [`schema.json`](schema.json)

Both are versioned with the skill so changes are auditable.

The extractor loads these canonical `config/skills` resources directly and requires `python3` for its standard-library response validator. Before provider access it rejects unsupported schema vocabulary. On return it requires a normally completed text candidate, a single JSON object, declared fields with the schema's required properties, types and enum values, and no duplicate JSON keys. It combines final text parts and ignores parts explicitly marked as thoughts. This is a structural contract, not factual review; a valid response remains an unverified extraction draft.

## Invocation

- `/process-transcripts` — interactive, one transcript at a time
- `/process-transcripts {path}` — process a specific transcript
- `/process-transcripts --all` — process every unprocessed transcript without per-file confirmation (use sparingly)

## Source location

Per [[../../rules/source-processing-pattern]], unprocessed transcripts live in `system/intake/`. Processed transcripts get moved to `system/transcripts/` (the archive) only after every downstream artifact is produced AND verified. Archive location alone is not completion evidence. Before retrying an archived source, reconcile its stable source identity, `processed-into` outputs and verification result. Resume only missing work; preserve existing verified notes and commitments. Do not create duplicates.

## Phases (per transcript)

### 1. Pre-flight

Read the transcript's frontmatter. Verify:
- `processed: false`
- `source-kind: transcript`
- `source-format` is one of: `granola-public-api`, `google-meet-transcript`, `gemini-meet-transcript`

If `processed: true`, verify referenced outputs before skipping. Missing or contradictory completion evidence needs reconciliation, not a duplicate extraction.

Separate confirmed attendees, people merely mentioned, and explicit absences. A missing speaker turn or a reference to a prior conversation establishes neither attendance nor absence. When the source does not resolve presence, say attendance is unconfirmed; do not turn that gap into “was not present.” Preserve tentative commercial terms as proposals. An empty recording or voicemail receives an explicit no-content disposition with source provenance, not an invented meeting or commitment. Review relevant recorded learnings before processing.

Pre-load vault context that downstream phases need (do this BEFORE the Gemini call so the wikilink resolution work in Phase 3 is fast):
- Glob `atlas/people/*.md` — list of person notes for wikilink resolution
- If meeting title or attendees match a known client, read `atlas/clients/{slug}/_brief.md` and `_status.md`
- If the meeting title matches a project, read its `_brief.md` and `_status.md`
- Recent `atlas/decisions/` (last 30 days) — for contradiction detection

### 2. Gemini extraction

Run the extraction script, capturing to a **unique** temp file (never a shared fixed path like `/tmp/extraction.json` — under Parallel backlog mode below, concurrent agents would clobber each other):

```bash
EXTRACT_JSON=$(mktemp "${TMPDIR:-/tmp}/extraction.XXXXXX") || exit 2
bash config/scripts/extract-transcript-gemini.sh {transcript-path} > "$EXTRACT_JSON"
```

Use the runtime's authorized temporary directory. In a confined workspace, set `TMPDIR` to an existing permitted directory before running the example; do not write outside that boundary just because the example has a default. Preserve the extractor's actual exit code before running diagnostic commands.

The script:
- Reads the prompt from `config/skills/process-transcripts/prompt.txt`
- Reads the schema from `config/skills/process-transcripts/schema.json`
- Calls Gemini 3.1 Flash Lite with `responseMimeType: application/json` + `responseSchema`
- Validates provider completion and the extraction JSON contract
- Emits token usage and reported model/response identifiers to stderr, extraction JSON to stdout

**On failure** (exit non-zero), see § Failure fallback below.

### 3. Mid-ingest checkpoint (high-stakes only)

If ANY of these apply, pause and present the Gemini-extracted summary + action item routing to the operator for confirmation BEFORE writing anything:

- Transcript title contains `[HIGH STAKES]` or `[CONFIDENTIAL]`
- Operator flagged it ahead of time
- Gemini returned `"sensitive": true`
- Gemini returned `"speaker_resolution_confidence": "low"`

For routine transcripts (Google Meet + Gemini high-confidence, work topics, sensitive=false), skip the checkpoint.

### 4. Wikilink resolution + vault-fit

Treat extraction as a draft. Before writing, compare each proposed attendee, decision, owner, deadline and commercial term with the verbatim source. Record the supporting source span and unresolved qualifications in the existing processing record. Review summary sentences and parentheticals as well as structured arrays: a correct attendee list does not validate an unsupported absence claim in the summary. An exact quote proves where the words came from, not that the proposed conclusion follows. Keep unsupported claims out of factual notes; retain the uncertainty or ask the operator when it prevents completion. JSON parsing and model confidence do not substitute for this review.

Take the extracted JSON and convert names → wikilinks:

- For each name in `attendees_present`, `decisions[].made_by`, `action_items[].owner`, `people_observations[].person`: check if `atlas/people/{slug}.md` exists.
  - Exists → use `[[slug]]`
  - Doesn't exist → use plain text first-name (per [[../../rules/no-fabrication]] — never guess a last name). Note in step 5 for person-note proposal evaluation.

- For project mentions or client mentions: same pattern. Use existing wikilinks where they resolve; plain text otherwise.

**Confidence-driven attribution:**

| `speaker_resolution_confidence` | What to write in the meeting note |
|---|---|
| `high` | Names directly as quoted, with wikilinks where available |
| `partial` | Names with `(inferred)` marker if name didn't appear literally in the transcript text. Create a `[REVIEW]` inbox item proposing the meeting note for operator double-check. |
| `low` | Keep diarization labels (`Speaker A`, `Speaker B`); create a `[REVIEW]` flagging the gap |

> [!warning] Diarized sources: Gemini's speaker mapping is a hypothesis, not ground truth
> On diarization-labeled transcripts (`source-format: granola-public-api`, or any source with `Speaker A/B` labels), Gemini's diarization-to-identity mapping is unreliable — a 40-transcript backlog run (2026-07-07) found it wrong or fully inverted on 3 of 4 substantive phone calls, including one where Gemini self-reported `high` confidence over utterance-level misattribution. For diarized sources: cap the effective `speaker_resolution_confidence` at `partial` regardless of what Gemini self-reports, and verify identity against vault cross-references (prior meeting notes with the same person, direct self-identifications in the verbatim, client status logs) before writing names. Name-resolved sources (Google Meet, Gemini Docs) don't need this cap.

### 5. Write the meeting note

`atlas/meetings/{YYYY-MM-DD}-{topic-slug}.md` per [[../../objects/meeting]]. Build from the source-reviewed extraction, not unverified JSON fields:

Required body sections (always present):
- **Summary** — source-supported outcome and what changed in this meeting
- **Key Topics** — reviewed topics with material qualifications preserved
- **Decisions** — choices actually made, with any stated conditions; distinguish proposals and unresolved choices. If the supplied source establishes no decision, say so within that source's scope. Inline routine decisions; durable ones get standalone notes per step 6.
- **Action Items** — reviewed commitments and unconfirmed assignments, with known owners and timing (full record; owner-based routing happens in step 7)
- **Source** — wikilink back to the intake transcript file

Optional sections (only when Gemini's arrays are non-empty):
- **Key Quotes** — `key_quotes[]`
- **People Observations** — `people_observations[]`
- **Open Questions** — `open_questions[]`

When the meeting relates to known work, follow its explicit project/entity links into relevant earlier meetings, decisions, actions and authorized communication records. Add a concise **Related Context** section when those sources explain the meeting's significance: what prior commitment it advances, changes or leaves unresolved, and which project is affected. Cite the specific dated record for each historical claim. Keep this context distinct from what was said or decided in the current transcript. A shared name or topic alone does not establish a relationship; an older note does not prove current status. Surface contradictions with both sources rather than silently choosing one. If relevant history is unavailable, state the limited coverage without treating missing context as proof that no earlier work exists.

Make the next step visible for each affected project when supported by the sources. Distinguish an existing agreed action from an agent's suggested next step; do not convert a suggestion into a commitment, invent an owner or deadline, or close older work merely because this meeting discusses it. Reuse and link existing actions instead of creating duplicates. External communication access still requires the task's authorization; this context pass does not grant permission to contact anyone.

Frontmatter:
- `date:` is the meeting occurrence date supported by source metadata or explicit confirmation, never today's processing date or an import timestamp. If absent, leave it unknown and record the missing context. A full meeting record cannot be finalized until required context is resolved; a scoped extraction may return the requested known facts without inventing extra metadata or claiming full processing completion. Do not fabricate a date-based filename for an undated source.
- `created:` and `last_updated:` describe note creation/editing, separate from meeting occurrence.
- `author:` names the actual verified authoring agent/runtime. Include the operator as an attendee only when their presence is supported by the source; importing a recording does not establish attendance.
- `sensitive: true` if Gemini flagged it (see [[../../objects/meeting]] § Confidentiality)
- `attendees:` from `attendees_present[]` (wikilinks where they resolve, plain text otherwise)
- `transcript:` a quoted wikilink string to the intake source, such as `transcript: "[[system/intake/source]]"`. Bare `[[source]]` is parsed by YAML as a nested list, not a link string. Parse generated frontmatter and check property types before final verification: `transcript` is a string and `attendees` is a list of strings. Link resolution alone does not validate these types.

### 6. Apply matching

Update each touched entity in the same pass per [[../../rules/matching]]:

- **Attendees with vault notes** — add substantive new context from `people_observations[]` with inline footnote citation to the meeting note. For attendees without a vault note, decide per the network-scope filter in [[../../objects/person]]:
  - Person is in the operator's direct network and meets ≥3-mention threshold → propose `[REVIEW]` for person note creation
  - Person is a clients'-client / homeowner / tertiary mention → mention by name in the meeting body; do NOT propose a person note
- **Decisions** — durable decisions (`durability: "durable"` from Gemini) get standalone `atlas/decisions/{date}-{slug}.md` notes. Routine ones stay inline.
- **Client / business `_status.md`** — substantive new context warrants an update.

### 7. Route action items by ownership

Read `commitment_status` before routing. Preserve `assigned-unconfirmed` as an assignment awaiting acceptance in the meeting and any related record; never rewrite it as the owner's promise. Establish affiliation independently of the assignment. If affiliation is unresolved, retain the known owner name with category `unknown` and use the ownership clarification route below. An unconfirmed assignment to the operator goes to the inbox as a request to clarify, with that status visible, not as a commitment they already made. A source-supported hard dependency may be tracked as waiting for a response, retaining the unconfirmed status; do not invent the dependency from the assignment alone.

Use Gemini's `owner_category` enum:

| `owner_category` | Where it goes |
|---|---|
| `operator` | `gtd/inbox/[ACTION] {slug}.md` — one file per commitment, no cap. This is the operator's own work; the inbox is where they clarify it out to `actions/next/` or a project. |
| `team` + `blocks_operator: true` | `gtd/actions/waiting/[WAITING] {slug}.md` — the operator is genuinely blocked on this delegated item. Keep waiting/ sparse: only hard blockers on the operator's forward progress qualify. Also stays inline on the meeting note. |
| `team` + `blocks_operator: false` (default) | **Inline on the meeting note only. Do NOT create an inbox entry.** Delegated team work is the team's to track, not the operator's GTD surface. The meeting note is the record. |
| `client_team` | Stays inline in the meeting note's `## Action Items` section. If material, also add to the client's `_status.md` under a "client-side open items" / equivalent section. **Do NOT create inbox entries.** |
| `client_client` | Inline only; no inbox, no client status. |
| `unknown` | Inline + create a `[QUESTION]` inbox item asking the operator to clarify ownership |

The meeting note's `## Action Items` section captures the **full record** — every commitment made in the room, regardless of owner. The inbox is the operator's GTD surface only: their own actions, plus the rare delegated item that hard-blocks them.

> [!warning] Delegated ≠ the operator's inbox
> The single biggest source of inbox bloat is routing every `team` commitment into the operator's inbox. Don't. The operator does not track their team's task lists. A `team` item only reaches them when `blocks_operator: true` — and that goes to `waiting/`, not the inbox. When in genuine doubt about whether something blocks them, default to inline-only (false); a missed blocker resurfaces naturally, an over-eager inbox does not self-clean.

The `[REVIEW]` flood-guard cap (≤7 per session) applies to `[REVIEW]` proposals (uncertain inferences). It does NOT apply to `[ACTION]` items.

### 8. Verify outputs before completion

Verify every required meeting, decision, substantive entity update and routed commitment exists, cites the source, and agrees with the source. Check the planned output list against the actual files; a missing required update keeps the run incomplete. No-content sources use an explicit disposition instead of an invented meeting.

Obtain the host-local Python path using `bash config/scripts/migrate.sh source-runtime` (runtime preparation is described in step 9). Run the read-only verification below on every created/updated knowledge note, repeating `--output` for each actual path:

```bash
"<runtime-python>" config/scripts/complete-transcript.py \
  --vault "$PWD" --verify-extraction \
  --source "system/intake/<source>.md" \
  --output "atlas/meetings/<meeting>.md"
```

This single read-only gate checks declared YAML properties, required outgoing output links and the retained source's links, including when extraction stops before archival. Use the actual source path, which may already be under `system/transcripts/`. It rejects duplicate property keys, a meeting transcript stored as a YAML list instead of a wikilink string, and invalid attendee property types. It preserves legacy notes without frontmatter and is not a complete object-schema validator. Output-only verification does not satisfy this gate. A plain-text source path is not a wikilink; a note with no outgoing wikilinks fails completion. Zero broken required references is necessary, but does not prove factual correctness. Review attendee attribution, ownership and uncertainty separately. Record source identity, output paths, verification result and any remaining work in a processing receipt in the existing session-log note (source ID/hash, output paths, verification, remaining work).

### 9. Mark complete, archive, and reconcile

Use `config/scripts/complete-transcript.py` for this transition after the factual review in step 8. Do not independently set processing flags or move the source: the helper checks links before archiving, refuses an existing archive destination, checks again after the move, and records source/output hashes plus the actual transition order. Its receipts and original source snapshots live in the existing `system/session-log/` directory. Establish one active processing writer before invoking it; it does not provide a cross-host lock.

Read `bash config/scripts/migrate.sh source-runtime` to obtain the host-local Python path and readiness. If missing, prepare it with `bash config/scripts/migrate.sh source-runtime --apply` when runtime installation is authorized. This installs pinned dependencies in an isolated environment, without replacing global Python packages. Use the returned `python` path for the commands below; do not silently fall back to a Python missing the required dependency.

```bash
"<runtime-python>" config/scripts/complete-transcript.py \
  --vault "$PWD" --source "system/intake/<source>.md" \
  --output "atlas/meetings/<meeting>.md" \
  --output "<other-created-or-updated-knowledge-note>.md" \
  --receipts "$PWD/system/session-log"
```

Repeat `--output` for the actual downstream knowledge notes; omit the second example argument if there are no additional notes. Record the returned receipt path in the session log. A no-content recording uses `--disposition no-content --reason "<source-grounded reason>"` with no `--output`; it still preserves the source and creates a completion receipt.

Before skipping a source on retry, run `--vault "$PWD" --verify-receipt "<receipt-path>"` with the same helper. Changed source/output hashes or an incomplete receipt require reconciliation, not duplicate extraction. For an interrupted helper operation, `--vault "$PWD" --resume-receipt "<receipt-path>"` resumes only when the recorded source and outputs remain unchanged; it preserves the earlier receipt and recovery snapshots. A refusal means inspect both locations and preserve newer work. Do not restore old output notes just to make hashes match.

Completion requires a successful helper receipt and current revalidation; `processed: true` or archive location alone is insufficient. The completion and receipt-verification paths repeat the declared-property checks as well as link checks. If a command fails, report the actual failed stage and leave the overall run incomplete. The helper verifies file state, declared properties and references, not factual accuracy or whether a no-content classification was justified.

### 10. Log

Finish the existing session-log processing record on both success and failure. Include:

- Source identity/link and observed source hash.
- Authoring agent/runtime; requested and reported model separately. Use `unknown` for unavailable model metadata rather than guessing from an alias.
- Execution context (live workflow or synthetic fixture), attempted stage/command, observed exit code, and which provider or verification steps actually ran.
- Output links and validated completion receipt, or an explicit incomplete state with no receipt.
- Remaining work justified by the observed failure. A simulated error tests handling; it does not establish that a live account or operator configuration is broken.

Preserve earlier attempts when adding a recovery result. Before returning, check that the record and final response agree about what ran, what changed and what remains.

Tool hooks may record some file operations, but coverage depends on the runtime and tool used; the Python completion command is not recognized by the current semantic event hook. An event row is supplementary observability, not proof that processing or this handoff record is complete.

## Failure fallback

If `extract-transcript-gemini.sh` exits non-zero:

| Exit code | Cause | Action |
|---|---|---|
| 1 | Gemini API error (rate limit, malformed request, transient) | Retry once with a 5s backoff. If still failing, fall back to step 2. |
| 2 | Hard failure (auth, prompt/schema missing, transcript unreadable) | Stop. Surface the error. No fallback — the operator needs to fix infra. |
| 3 | Provider response is incomplete or fails the extraction JSON contract | Retry once. If still failing, use the documented extraction fallback in step 2; do not treat the invalid response as a completed extraction. |

**Step 2 (Sonnet fallback):** When that runtime and delegation capability are available and authorized, delegate to the `knowledge-management` subagent with `model: sonnet`. Give it the current prompt/schema and a self-contained task per the delegation pattern below. Preserve commitment status, uncertain identity/affiliation and source qualifications in every output. A fallback model is not presumed reliable: it must pass the same factual review, output verification and completion gates. If the configured fallback is unavailable, report that limitation rather than silently substituting another model.

**If Sonnet also fails:** Stop, surface to the operator. Do not silently downgrade further.

## Delegation pattern (when Gemini fallback fires, or for batch processing)

For long transcripts (≥500 utterances), batches, or an operator request to keep the main session light, use the configured `knowledge-management` / Sonnet delegation path when available and authorized. Otherwise use the supported sequential workflow and record its actual runtime; do not claim a delegated run occurred. Model choice does not relax the source or completion requirements.

The subagent prompt MUST be fully self-contained — it sees zero of the main session's context. Required elements:

1. **The intake file paths.**
2. **Operator-confirmed attendee list** (if known) — the actual people in the room, not just the calendar invitees.
3. **Calendar invitees who should NOT be treated as present** (e.g., a calendar-only invitee on a client design-group meeting).
4. **Existing person-note paths** for attendees, so the agent knows which wikilinks resolve.
5. **Client folder and active-project folder paths** for matching cross-updates.
6. **Rule files to read** — `config/objects/meeting.md`, `config/rules/source-processing-pattern.md`, `config/rules/matching.md`, `config/rules/no-fabrication.md`, `config/rules/double-entry-knowledge.md`, `config/rules/writing-style.md`, plus this skill.
7. **Known sensitive content** the meeting touches — so the agent sets `sensitive: true` proactively.
8. **Required output schema and write ownership** — the current extraction schema including commitment status; exact allowed output paths; shared paths that must remain untouched; and the factual, property/link and completion-receipt checks from steps 8–9. Assign source completion to the coordinator in parallel mode.
9. **Final-report shape** — speaker resolution summary, files produced/updated, anything unexpected, open items.

The main session's role after dispatch: read every resulting note against its source, review consequential matching updates and commitment qualifications, and verify the steps 8–9 evidence. A successful subagent return or clean link check alone does not establish completion.

### Parallel backlog mode

The delegation above runs **one** subagent at a time (or sequentially). When the backlog is large — **≥10 unprocessed transcripts, or explicit operator request** — fan out to multiple subagents in parallel. Parallel writers with no file locking means last-writer-wins clobbering is a real hazard, so this mode trades raw parallelism for a strict ownership boundary. Do NOT use it for the normal interactive one-at-a-time flow — that stays simple and same-pass.

**0. Extraction order and failure handling match the sequential path.** Each parallel task must attempt `extract-transcript-gemini.sh` first and apply the exit-code-specific table above. Exit 2 stops that transcript without retry or fallback. Record the actual extraction path, requested/reported model, outcome and remaining work in its manifest. Preserve failed attempts; never treat fallback selection or a successful model return as factual acceptance.

**1. Partition into disjoint clusters.** Group the transcripts so no two clusters are expected to touch the same entity (e.g. all of one client's meetings in one cluster; each team member's 1:1s in their own cluster). Build a quick preflight entity map (Glob `atlas/people/*`, `atlas/clients/*`, `gtd/projects/*`) so the partition is grounded, not guessed.

**2. Ownership is a path denylist, not an entity inference.** Each parallel agent owns — and may write — only files **uniquely** produced by its cluster:
- its meeting notes (`atlas/meetings/{date}-{slug}.md`)
- its uniquely-named inbox/waiting items (`[ACTION]`/`[REVIEW]`/`[QUESTION]`/`[WAITING]`)
- standalone decision notes with unique slugs
- a processing record in the existing `system/session-log/` directory for its cluster

Workers leave transcript bytes, processing flags and locations unchanged. The sequential coordinator owns completion through the helper after shared updates and factual review; workers never archive a source independently.

Everything else is **shared and off-limits while fanning out** — by path, regardless of whether the agent thinks it "owns" the entity (agents can be wrong about identity):
- any `atlas/clients/*/_status.md`, `atlas/businesses/*/_status.md`, `gtd/projects/*/_status.md`, any `_brief.md`
- any person note that more than one cluster could touch
- any shared index/state/log file

**3. Shared updates come back as durable findings — not chat.** A parallel agent NEVER edits a shared file. It records each proposed update in its uniquely named processing note under the existing `system/session-log/` directory, not a new findings folder or only its final chat response. Each finding carries the target path, entity, source meeting/transcript identity and hash, source-supported occurrence date (unknown when absent), supporting source span, proposed text, unresolved qualifications and application status. If a cross-cluster entity is discovered, preserve both findings and flag the overlap for the coordinator.

**4. One sequential consolidation pass.** After all workers have finished or stopped, the coordinator reconciles their actual outputs and source hashes. Apply only source-supported findings, one target at a time, ordered by known meeting occurrence dates per the matching rule. Do not invent chronology for undated sources. Compare the target's current contents before each update, preserve concurrent edits/conflicts, and deduplicate by source identity plus claim. Record applied, rejected and unresolved findings in the processing records. Repeating the pass must not create duplicate claims or commitments.

**5. Completion is owned and verified per source.** Once all required downstream work for a source is complete and factually reviewed, the coordinator invokes the same `complete-transcript.py` transition as step 9, with every actual required output. Completion requires the helper receipt and current receipt revalidation, not flags, archive location or a worker manifest alone. For interrupted work, inspect both intake/archive locations and existing receipts, resume only missing work through the documented recovery path, and preserve newer outputs. Missing shared updates keep that source incomplete. An unsupported or ambiguous finding stays unresolved with a linked record; it is not silently applied or discarded to obtain a completion flag.

## Confidentiality

If the meeting note carries `sensitive: true` (set by Gemini or by operator flag), apply confidentiality conventions per [[../../objects/meeting]] § Confidentiality:
- Internal traceability stays — meeting note links to transcript and people as usual
- Any content draft proposed from this meeting must anonymize identifying details
- Add a `[QUESTION]` if any insight is unusually identifiable and you're unsure whether it can be shared externally

Use the Gemini path within the operator's existing provider authorization and any applicable project restrictions. The sensitive flag does not by itself grant or revoke that authorization. Storing a transcript in the vault is not evidence that every external destination is permitted; carry explicit restrictions into delegated tasks.

## Runtime evidence

Record actual requested/reported model, token usage, elapsed time, extraction path and verification outcomes in the processing record. Missing provider metadata stays unknown. Historical timing, price estimates or model reputation are not current quality evidence; compare frozen evaluations for the actual runtime and skill revision before enabling automation.

## What NOT to do

- **Don't fabricate attendees.** If Gemini returned a name not in the transcript, drop it. Per [[../../rules/no-fabrication]].
- **Don't treat the Granola/Google `attendees-from-source` field as ground truth.** That's the calendar invite list, not actual presence. Use Gemini's `attendees_present` (which is grounded in transcript speaker turns).
- **Gemini-import `calendar-invitees` records invitations, not attendance.** Preserve its supplied names, emails and response states as source metadata; do not infer names from email local parts or turn accepted invitations into presence. An empty `attendees-from-source` means attendance was not established by the importer, not that nobody attended. Check the transcript before accepting the extractor's attendance claims.
- **Don't fill timeline gaps.** If the transcript jumps topics, don't reconstruct what was missed.
- **Don't guess speaker names when Gemini returned `low` confidence.** Plain `Speaker X (unidentified)` + `[REVIEW]` beats fabrication.
- **Don't create person notes for clients' clients** (homeowners, prospects, tertiary mentions). Per [[../../objects/person]] network-scope filter.
- **Don't create inbox `[ACTION]` items for `client_team`, `client_client`, or non-blocking `team` commitments.** Per [[../../objects/action]] ownership filter and Gemini's owner_category enum. Only `operator` items create inbox `[ACTION]`s; blocking `team` items create a `waiting/` entry.
- **Don't apply the `[REVIEW]` flood-guard cap to `[ACTION]` items** — every operator commitment becomes its own file.
- **Don't process a transcript without operator confirmation in interactive mode.**
- **Don't synthesize from Gemini's Tab-1 Notes summary or any pre-baked summary** — the pipeline runs against the verbatim only. Per [[../../rules/source-processing-pattern]].
- **Don't move the transcript out of `system/intake/`** until every downstream artifact is produced AND verified clean.
- **Don't tweak `prompt.txt` or `schema.json` without re-testing.** Both live next to this file and are easy to iterate, but every change should be smoke-tested on at least one Granola + one Google Meet transcript before being treated as production.
