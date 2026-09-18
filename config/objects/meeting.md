---
type: object-type-definition
name: meeting
zone: atlas
location: atlas/meetings/
shape: atomic
naming: "{YYYY-MM-DD}-{topic-slug}"
version: 1.1
---

# Object Type: meeting

> [!info] Schema discipline
> Per [[type-scaffolding]], this object's schema must be designed intentionally and revised via `iterate-instance-then-propagate-schema`. V1.1 was derived from an external-coaching meeting dogfood (2026-05). Future revisions should follow the same instance-grounded pattern.

A meeting is a single record of a real interaction with one or more other people. Always traceable to a transcript, a live session, or an operator-direct dictation. The atlas/meetings/ folder is the canonical record of "what was said and decided" — every other zone (atlas/people, atlas/decisions, gtd/inbox, atlas/businesses) draws context from here.

Meetings are atomic notes (one file per meeting). Universal — ships pre-built with WorkDesk OS.

## Frontmatter contract

### Required fields

| Field | Type | Notes |
|---|---|---|
| `type` | literal | Must be `meeting` |
| `date` | `YYYY-MM-DD` | When the meeting occurred, supported by source metadata or explicit confirmation. Never substitute the import, processing or current date. Unknown dates remain unfilled and are reported as missing context; they prevent finalizing a full meeting record. |
| `status` | enum | See [§ Lifecycle](#lifecycle) |
| `meeting-type` | enum | See [§ Meeting-type enum](#meeting-type-enum) |
| `attendees` | list | Wikilinks for attendees with existing `atlas/people/` notes; plain strings otherwise. See [§ Attendee discipline](#attendee-discipline) |
| `source` | string or wikilink | Where the meeting record came from. See [§ Source field values](#source-field-values) |
| `created` | `YYYY-MM-DD` | When the meeting note was created |
| `last_updated` | `YYYY-MM-DD` | Last edit |
| `author` | string | The verified authoring agent/runtime (for example `claude` or `codex`), or `operator` for operator-written records. Never attribute one agent’s work to another. |

### Optional / contextual fields

| Field | Type | When to include |
|---|---|---|
| `transcript` | wikilink | Link to `[[system/transcripts/{slug}]]` when a verbatim transcript exists. Distinct from `source` — `transcript` is the verbatim record; `source` is what triggered the meeting note's creation (they often point at the same file but not always — e.g., calendar+confirmation has no transcript). |
| `client` | wikilink | `[[atlas/clients/{slug}]]` when the meeting is client-related |
| `business` | wikilink | `[[atlas/businesses/{slug}]]` when the meeting is business-owned (e.g., internal Acme Consulting meetings) |
| `companies` | list | Wikilinks to `atlas/companies/{slug}` (or plain strings if no company schema yet) when multiple non-client companies are involved |
| `sensitive` | boolean | Default `false`. Set `true` for meetings discussing personnel decisions, financials, client-confidential information, or other content requiring [§ Confidentiality](#confidentiality) conventions on derived output |

## Lifecycle

| State | Meaning | Transitions out |
|---|---|---|
| `scheduled` | Meeting is on the calendar; note created in advance | → `complete` (meeting happened) <br>→ `did-not-occur` (with reason) <br>→ `partial` (ended early) |
| `complete` | Meeting happened; note processed | (terminal) |
| `did-not-occur` | Meeting was scheduled but didn't happen; preserved as record with reason | (terminal) |
| `partial` | Meeting happened but was cut short or aborted; record reflects what was covered | (terminal) |

`did-not-occur` meetings still get a note — they document the non-occurrence and reason (per the 2026-04-22 operator + Alex 1on1 legacy precedent). Body is minimal but the record exists.

## Meeting-type enum

The `meeting-type` field captures the **relational context** of the meeting (who is meeting with whom), not the format. Eight allowed values:

| Value | Definition |
|---|---|
| `client-1on1` | One-on-one with a single client contact (e.g., operator + Jordan Lee) |
| `client-group` | Meeting with multiple client team members; collaborative, often recurring (e.g., operator + Acme Corp design team) |
| `client-session` | Facilitated session WITH a client — workshop, training, presentation (e.g., Acme Corp AI Workshop Executive Group) |
| `internal-1on1` | One-on-one within the operator's own team / `acme-consulting` (e.g., operator + Sam) |
| `internal-team` | The operator's team-wide meeting (e.g., Acme Consulting Check-In) |
| `external` | Meeting with an external party that's not a client — networking, vendor, prospect, peer |
| `external-coaching` | Recurring coaching session with an external coach or mentor (e.g., operator ↔ Pat Morgan) |
| `workshop` | Group session for non-client audience (e.g., COE workshop, conference talk) |

When in doubt, pick the most specific applicable value. If none fits, propose a new enum value through a schema revision — don't silently invent a one-off.

## Source field values

The `source:` field documents what triggered the meeting note's creation. Common values:

- `"[[system/transcripts/{slug}]]"` — wikilink to a verbatim transcript that arrived in `system/transcripts/`
- `granola` — meeting captured by Granola, transcript exists in the granola archive
- `operator-paste` — operator pasted raw text into an agent session
- `operator-direct` — operator dictated the meeting in conversation (no transcript)
- `calendar-event+operator-confirmation` — meeting record built from a calendar event plus operator memory (often used for `did-not-occur` or notes-only meetings)

When `transcript:` is set, `source:` usually points at the same wikilink. When there's no transcript, `source:` records how the agent or operator built the record.

## Attendee discipline

Attendees go in frontmatter as a YAML list. Per [[double-entry-knowledge]] and [[no-fabrication]]:

- **Wikilinks** for attendees with existing notes in `atlas/people/` — e.g., `"[[jordan-lee]]"`
- **Plain strings** for attendees without notes — e.g., `"Pat Morgan"`
- **Never fabricate wikilinks** to person notes that don't exist. Plain strings upgrade to wikilinks when the person note is later created.
- **The operator** is an attendee only when the source or explicit operator confirmation establishes attendance. Owning or importing a recording is not attendance evidence. Do not add a self-wikilink when the vault has no operator self-note.
- **Unconfirmed presence is not absence.** A person mentioned in a prior conversation, or without a recorded speaker turn, has unknown attendance unless the source resolves it. State an absence only when the source explicitly supports it; otherwise keep attendance unconfirmed in both metadata and prose.

Don't include attendees in the body unless the frontmatter list is incomplete or needs annotation (e.g., legacy practice of listing them under `## Attendees` with role notes). For most meetings, frontmatter is sufficient.

## Body sections

### Required (always present)

| Section | Purpose | Format |
|---|---|---|
| `## Summary` | One paragraph synthesis of the meeting — what happened, what mattered, what's next | Prose paragraph |
| `## Key Topics` | What was discussed, structured by topic with subheadings | Sub-headed `### {Topic}` sections; each topic gets a short paragraph or bullets |
| `## Decisions` | What was decided; standalone-worthy decisions link to `atlas/decisions/{slug}` | Bulleted list; sub-section for open / pending decisions when applicable |
| `## Action Items` | What needs to happen next; routed through `gtd/inbox/` as `[ACTION]` proposals | Checkbox bullets with **owner** in bold and optional deadline |
| `## Source` | Provenance — links to transcript, operator dictation context, processing date | Bulleted list of source references |

### Optional (header preserved when applicable, omitted when not)

| Section | When to include | Format |
|---|---|---|
| `## Key Quotes` | Memorable, content-worthy quotes — usually from workshops, client sessions, coaching | Bulleted list; speaker attribution at the end |
| `## Content Candidates` | Frameworks, analogies, observations from the meeting that could become content. Subject to confidentiality per `sensitive:` flag | Bulleted list with one-line description per candidate |
| `## People Observations` | Per-person observations from the meeting — operator's read on individuals, patterns, watch-outs | Sub-headed `### {Person}` sections |
| `## Confidentiality` | When `sensitive: true` is set — explicit note on what protections apply to derived content | Short paragraph |

### Header-preservation rule (per [[type-scaffolding]])

If a section IS applicable to this meeting but has no content yet, preserve the header with a one-line placeholder (`TBD — populate when known.` or `Not applicable to this meeting.`). If a section is NOT applicable, omit it entirely. The optional sections above can be entirely absent for, e.g., a quick `did-not-occur` 1:1.

## Detection / Creation workflow

Meetings are created through one of three paths, in priority order:

### Path A — Auto-derivation via `/process-transcripts` (primary)

The expected path for meetings with transcripts:

1. Transcript arrives in `system/transcripts/` (via Granola export, manual upload, or other source)
2. Session-entry scan surfaces the unprocessed transcript
3. `/process-transcripts` proposes extraction into a meeting note + matching cross-updates
4. **Operator confirms** the proposal in `gtd/inbox/` (per [[matching]] flood-guard discipline)
5. Claude creates `atlas/meetings/{date}-{topic-slug}.md` per this schema
6. Claude applies matching (see [§ Matching](#matching))
7. Transcript frontmatter updated: `processed: true`, `processed-into: ["[[atlas/meetings/{slug}]]"]`

### Path B — Operator-direct dictation

When operator pastes a transcript or describes a meeting in a Claude Code session:

1. Operator pastes raw transcript OR describes the meeting verbally
2. Claude writes the transcript to `system/transcripts/{date}-{slug}.md` with `source-format: operator-paste`
3. Claude creates `atlas/meetings/{date}-{slug}.md` per this schema
4. Claude applies matching
5. Transcript frontmatter updated with `processed: true` + backlink

### Path C — Calendar + confirmation (no transcript)

For `did-not-occur`, notes-only meetings, or meetings without recordings:

1. Operator references a calendar event ("the Alex 1:1 yesterday")
2. Claude creates `atlas/meetings/{date}-{slug}.md` with `source: calendar-event+operator-confirmation`
3. `transcript: none` in frontmatter
4. Body is minimal — Summary + Source are required; Decisions / Action Items / others as applicable
5. Matching applied per [§ Matching](#matching)

## Matching

When a meeting note is created, [[matching]] applies invisibly to:

- **Each attendee with a vault note** — update their `atlas/people/{slug}.md` with relevant substantive context (the meeting's wikilink, key observations, role on this meeting's outcomes). Pure mentions ("Sarah was also on the call") don't trigger updates per the matching rule.
- **Standalone-worthy decisions** — create `atlas/decisions/{date}-{slug}.md` for each decision that meets the standalone criteria (high-stakes, cross-project, framework-shaping, or operator-flagged). Routine decisions stay inline on the meeting note.
- **Action items** — route each as a `[ACTION]` proposal in `gtd/inbox/` (subject to the 7-per-session flood guard). Each inbox item links back to the meeting note.
- **Business / client / companies** — when substantive new context emerges, update `_status.md` of the linked business/client. Don't update for passing mentions.
- **Transcript backlink** — set `processed: true` and `processed-into: [...]` on the source transcript.

Matching gaps (e.g., an attendee without a person note, a referenced entity without a folder) get [REVIEW] inbox notifications — not silent skips and not speculative stubs. See [[instance-scaffolding]] for the conditional-matching pattern.

## Confidentiality

When `sensitive: true` is set on the meeting (or inherited from a sensitive source transcript):

- **Internal traceability is preserved.** Full names, identifying details, financials, personnel decisions remain in the meeting note for vault use.
- **Externally-derived content must anonymize.** Podcast clips, blog posts, social-content drafts produced from this meeting strip identifying details (replace names with roles, scrub financials, generalize specifics).
- **Default `sensitive: false`** — flag `true` explicitly when content warrants. Triggers worth `true`:
  - Personnel decisions (hires, terminations, role changes, performance issues)
  - Financial specifics (salaries, revenue, margins)
  - Client-confidential information per the engagement terms
  - Strategic decisions not yet public

Confidentiality applies to derived output, not to the meeting note itself.

## Creating an instance

Most meetings will be created automatically via Path A. When manual creation is needed, follow these steps:

### Step 1 — Gather required information

Before writing the note, confirm:

- Date of the meeting (YYYY-MM-DD)
- Attendees (and which have existing person notes)
- Meeting type (one of the 8 enum values)
- Source — transcript path, operator-paste, or calendar+confirmation
- Status (usually `complete` for processed meetings)
- Whether `sensitive: true` applies

If a transcript exists but isn't yet in `system/transcripts/`, save it there first per the [[transcript]] source seed.

### Step 2 — Write the note

Follow the body section structure above. Required sections always; optional sections only when applicable. Use the V1.1 frontmatter contract.

### Step 3 — Apply matching

Update each entity touched per the [[matching]] rule. Create decision notes for standalone-worthy decisions. Route action items to `gtd/inbox/`. Update the source transcript's `processed: true` + backlink.

### Step 4 — Verify

Run `bash config/scripts/check-wikilinks.sh atlas/meetings/{slug}.md` — zero broken wikilinks required before declaring done. Per [[double-entry-knowledge]], any plain-text reference to an entity that DOES have a vault note should be wikilinked; entities without notes stay plain text.

### Step 5 — Notify operator (when applicable)

If matching surfaces gaps requiring operator attention (e.g., missing person note for a key attendee, ambiguous entity reference, conflicting information per [[matching]]'s conflict-handling clause), file a `[REVIEW]` inbox item rather than fabricating.

## What NOT to do

- Don't auto-create meeting notes from calendar events alone — always require operator confirmation (per the V1.0 stub discipline).
- Don't fabricate attendees from calendar invites when the transcript doesn't confirm them. Per [[no-fabrication]] — "Attendees not captured in transcript" is better than guessing.
- Don't strip the Sources section. Provenance survives every edit per [[source-documentation]].
- Don't create speculative person notes for attendees just to make wikilinks work. Plain strings are fine until the operator decides the person warrants their own note.
- Don't promote every decision to `atlas/decisions/`. Routine decisions stay inline on the meeting note; only standalone-worthy ones get their own file.
- Don't include "Matching impacts" as a permanent section in the meeting note. Matching happens invisibly; gaps surface as `[REVIEW]` inbox items. A transient "Matching impacts (proposed)" section is acceptable while a meeting note is awaiting operator approval before matching is applied, but it should be removed once matching is executed.

## Sources

This V1.1 schema deepening derived from:

- Operator instruction, 2026-05-17 Claude Code session (desk-setup project, meeting-schema dogfood)
- `../../atlas/meetings/2026-05-15-acme-consulting-coaching` — the dogfood meeting note that surfaced all the discipline above (attendee handling for non-vault people, `external-coaching` enum value, optional-section pattern, transient matching-impacts section, confidentiality rule application)
- Legacy meeting notes sampled from the operator's prior vault (2026-01 through 2026-04)
- [[type-scaffolding]] — schema design discipline (iterate-instance-then-propagate-schema)
- [[instance-scaffolding]] — conditional-matching pattern for entities without notes yet
- [[matching]] — cross-update discipline at processing time
- V1.0 stub of this file (2026-05-09 era) — preserved frontmatter shape and basic body sections; V1.1 extends rather than replaces
