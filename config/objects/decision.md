---
type: object-type-definition
name: decision
zone: atlas
location: atlas/decisions/
shape: atomic
naming: "{YYYY-MM-DD}-{topic-slug}"
version: 1.1
---

# Object Type: decision

> [!info] Schema discipline
> Per [[type-scaffolding]], this object's schema must be designed intentionally and revised via `iterate-instance-then-propagate-schema`. V1.1 was derived from three instances created 2026-05-17 (`../../atlas/decisions/2026-05-15-acme-consulting-direction-reframe`, `../../atlas/decisions/2026-05-15-acme-consulting-team-restructuring-30-60-90`, `../../atlas/decisions/2026-05-15-coe-workshops-as-qualification-funnel`) plus legacy decision notes sampled from the operator's prior vault. Future revisions should follow the same instance-grounded pattern.

A decision is a choice made — with rationale, sourced to where it was made, with explicit reversal conditions where applicable. Decisions live separately from the meetings that produced them when they're standalone-worthy (strategic, durable, cross-project, or framework-shaping). Routine meeting-level decisions stay inline on the meeting note.

The `atlas/decisions/` folder is the canonical record of *"what was decided, why, and when would we revisit it."* It is the load-bearing source of truth for: business briefs (Direction sections), project plans (foundational assumptions), team memos (distribution artifacts), and future-self orientation ("why did we decide this six months ago?").

Decisions are atomic notes (one file per decision). Universal — ships pre-built with WorkDesk OS.

## Frontmatter contract

### Required fields

| Field | Type | Notes |
|---|---|---|
| `type` | literal | Must be `decision` |
| `status` | enum | See [§ Lifecycle](#lifecycle) |
| `date` | `YYYY-MM-DD` | When the decision was made (not when the note was written) |
| `source` | wikilink or string | The meeting, session, or interaction where the decision was made. Almost always a `[[../meetings/{slug}]]` link; occasionally `operator-direct` for decisions made outside any captured interaction |
| `participants` | list | Decision-maker + others in the conversation. Wikilinks for participants with `atlas/people/` notes; plain strings otherwise. The operator does NOT get a self-wikilink (same discipline as the meeting type) |
| `created` | `YYYY-MM-DD` | When the decision note was created |
| `last_updated` | `YYYY-MM-DD` | Last edit |
| `author` | string | `claude` or `operator` |

### Optional / contextual fields

| Field | Type | When to include |
|---|---|---|
| `affects` | list of wikilinks | Entities that need updates per [[matching]] — `[[../businesses/{slug}/_brief]]`, `[[../projects/{slug}/_status]]`, etc. Drives the matching application. |
| `business` | wikilink | `[[../businesses/{slug}]]` when decision is business-scoped |
| `client` | wikilink | `[[../clients/{slug}]]` when decision is client-scoped |
| `superseded-by` | wikilink | When `status: superseded` — link to the later decision that replaced this one |
| `supersedes` | wikilink | When this decision replaced a prior one — link to the prior (which should have `status: superseded` + `superseded-by:` set to this note) |
| `reverses` | wikilink | When this decision undoes a prior one — link to the prior (which gets `status: reversed`) |
| `sensitive` | boolean | Default `false`. Set `true` for decisions involving personnel, financials, client-confidential, or strategic info not yet public. Same confidentiality conventions as the meeting type apply to derived output |

## Lifecycle

| State | Meaning | Transitions out |
|---|---|---|
| `active` | Decision is in effect | → `reversed` (formally undone by a later decision) <br>→ `superseded` (replaced by a later decision with different content) |
| `reversed` | Formally undone; a follow-up decision captures the reversal. The reversing decision has `reverses:` pointing here | (terminal) |
| `superseded` | Replaced by a later decision; `superseded-by:` field points to the successor. Used when the decision evolves rather than reverses (e.g., V1 → V2 of the same call) | (terminal) |

The distinction between `reversed` and `superseded`:
- **`reversed`** — "we changed our minds; this is no longer the right call"
- **`superseded`** — "this was the right call at the time; we now have a more current decision on the same question"

When in doubt, use `superseded` (less judgmental, preserves the prior decision's rationale as a snapshot of what made sense then).

## Standalone-worthy criteria

A decision merits its own note in `atlas/decisions/` (vs staying inline on the source meeting note) when ANY of:

- **Strategic direction** — changes positioning, scope, business model, or core methodology
- **Personnel** — hires, terminations, role changes, team restructuring (often `sensitive: true`)
- **Cross-project / cross-business** — affects 2+ projects, businesses, or areas
- **Framework-shaping** — defines a methodology, standard, or pattern that future work will inherit
- **Has explicit reversal conditions** — if you can name "we'd revisit this if X," it's durable enough to track
- **Future-self rationale dependency** — reading the decision in 6 months would require the rationale to make sense
- **Operator says "this is a decision"** — explicit flag during conversation

Routine meeting-level decisions ("the operator to facilitate next Tuesday's meeting," "send the docs by Friday") stay inline on the meeting note's Decisions section. Don't promote them.

## Body sections

### Required (always present)

| Section | Purpose | Format |
|---|---|---|
| `## Decision` | What was decided — crisp, one-sentence or one-paragraph statement | Prose; no bullets at this level |
| `## Rationale` | Why; paths considered and rejected; quotes from source when they illuminate; the "if we read this six months later, this is what we'd need to understand" content | Mix of prose and bullets as appropriate |
| `## Implications` | What changes downstream — concrete, named, listed | Bulleted list |
| `## Reversal conditions` | When would we revisit this? Specific, measurable, time-bound where possible | Bulleted list. If genuinely not applicable, write: *"Not applicable — operational settlement, not a reversible choice."* Don't omit the header. |
| `## Sources` | Provenance — the meeting / session that produced the decision, plus any supporting context | Bulleted list of source references |

### Optional (header preserved when applicable, omitted when not)

| Section | When to include | Format |
|---|---|---|
| `## Context` | When the need for the decision isn't obvious from the source alone (e.g., decision was made in a brief meeting but resolves a long-running tension) | Prose paragraph |
| `## People involved` | When roles or ownership are richer than the frontmatter `participants:` list can express (e.g., who-owns-what after the decision) | Sub-headed or bulleted list with role notes |
| `## Team memo` | When the decision needs to be distributed to a team and you want to draft the communication artifact alongside the decision record | Prose; written in the operator's voice |
| `## Related decisions` | When this decision interlocks with others (same session, same theme, cross-referencing) | Bulleted list of `[[..]]` wikilinks with a one-line note per |

### Header-preservation rule (per [[type-scaffolding]])

If a section IS applicable but body is TBD, preserve the header with a placeholder. If a section is NOT applicable (e.g., no team memo needed for a personal-scoped decision), omit it entirely. Required sections always get headers, even if the body is "Not applicable" (especially `## Reversal conditions`).

## Detection / Creation workflow

Decisions are created through one of three paths:

### Path A — Auto-derivation from a meeting note

The primary path:

1. A meeting note's `## Decisions` section contains an item that meets standalone-worthy criteria
2. **Operator confirms** the promotion (per the [[matching]] flood-guard discipline — don't promote silently)
3. Claude creates `atlas/decisions/{date}-{topic-slug}.md` per this schema
4. The meeting note's `## Decisions` section is updated to link to the new decision note (wikilink replaces the plain-text bullet)
5. Matching applied to entities in `affects:` (see [§ Matching](#matching))

### Path B — Operator-direct dictation

When operator declares a decision in conversation without a captured meeting:

1. Operator says: "we decided X" or "I've decided to Y"
2. Claude verifies the standalone-worthy criteria — if borderline, ask: "should this be a standalone decision note, or inline somewhere?"
3. On confirmation, Claude creates the decision note with `source: operator-direct` (or a wikilink to the session log if `/extract` was run for that session)
4. Matching applied

### Path C — Standalone deliberation

When operator dictates a decision from reflection outside any specific interaction (e.g., overnight thinking that produces a conclusion):

1. Operator declares the decision and its source ("I've been thinking about X all weekend; I've decided Y")
2. Claude creates the note with `source: operator-direct` and `participants: [the operator]` (just the operator)
3. The Context section captures the deliberation arc, since no meeting documents it
4. Matching applied

## Matching

When a decision note is created or updated, [[matching]] applies invisibly to:

- **Each entity in `affects:`** — update the entity's `_status.md` (or equivalent) with the decision wikilink + a one-line summary of the decision's relevance to that entity. Don't duplicate the full decision content; the decision note is the single source of truth, the `affects` entries link to it.
- **Source meeting note's `## Decisions` section** — if Path A, the meeting note's plain-text bullet for this decision gets replaced with a wikilink to the new decision note (already handled in step 4 of Path A above).
- **Prior decision being reversed or superseded** — if `reverses:` or `supersedes:` is set, update the prior decision's `status:` to `reversed` or `superseded`, and set its `superseded-by:` to the current decision's wikilink.
- **Cross-referenced sibling decisions** — when decisions in the same session reference each other (the 2026-05-15 cluster is the canonical example), use the `## Related decisions` section to make the links explicit. No status changes between siblings — they coexist.

Matching gaps surface as `[REVIEW]` inbox items (e.g., "decision affects `atlas/projects/X` but that project folder doesn't exist yet"). Don't create speculative stubs; flag and defer per [[instance-scaffolding]].

## Confidentiality

When `sensitive: true` is set (or inherited from a `sensitive: true` source meeting):

- **Internal traceability is preserved.** Full names, financial details, personnel specifics remain in the decision note for vault use.
- **Externally-derived content must anonymize.** Any podcast, blog, or social content drawing on the decision strips identifying details per the same conventions as the meeting type.
- **Default `sensitive: false`** — flag explicitly when the decision involves:
  - Personnel decisions (hires, terminations, role changes, performance issues)
  - Financial specifics (revenue, margins, salaries)
  - Client-confidential information
  - Strategic decisions not yet public

## Creating an instance

Most decisions arrive via Path A (auto-derivation from a meeting note). When manual creation is needed:

### Step 1 — Verify standalone-worthy

Before creating the note, confirm the decision meets at least one of the [§ Standalone-worthy criteria](#standalone-worthy-criteria). If borderline, ask the operator. Don't promote routine meeting-level decisions.

### Step 2 — Gather required information

- The decision itself (crisp statement)
- The source (meeting wikilink, session log, or `operator-direct`)
- Participants (decision-maker + others in the conversation)
- Affected entities (for the `affects:` field — drives matching)
- Whether `sensitive: true` applies
- Whether this reverses or supersedes a prior decision

### Step 3 — Write the note

Follow the required body sections (Decision / Rationale / Implications / Reversal conditions / Sources). Add optional sections only when applicable. Use the V1.1 frontmatter contract.

### Step 4 — Apply matching

Update each entity in `affects:` per the [[matching]] rule. If reverses or supersedes, update the prior decision's status. If Path A, update the source meeting note's `## Decisions` section to link here.

### Step 5 — Verify

Run `bash config/scripts/check-wikilinks.sh atlas/decisions/{slug}.md` — zero broken wikilinks required before declaring done. Run again across affected entities to confirm matching produced no broken links.

### Step 6 — Notify operator when applicable

If matching surfaces gaps (an `affects:` target doesn't exist, an entity needs operator clarification before update), file a `[REVIEW]` inbox item rather than fabricating.

## What NOT to do

- Don't promote routine meeting-level decisions to standalone notes. They stay inline on the meeting note's `## Decisions` section. Promotion is a deliberate choice, not a default.
- Don't fabricate rationale. If the source captured the decision but not the reasoning, write "Rationale not captured in source" rather than constructing plausible reasoning. Per [[no-fabrication]].
- Don't omit the `## Reversal conditions` header. If genuinely not applicable, write the "Not applicable — operational settlement, not a reversible choice." placeholder. The discipline of considering reversibility is the whole point of the section.
- Don't silently overwrite a prior decision when a new one supersedes it. The prior decision keeps its content (as a snapshot of what made sense then); only its status field updates, with `superseded-by:` pointing to the successor.
- Don't update `affects:` entities by duplicating the decision content. Link to the decision; the decision note is the single source of truth.
- Don't promote a decision to a standalone note without operator confirmation when in doubt. Borderline cases get flagged, not auto-promoted.
- Don't include matching as a body section in the decision note. Matching happens to OTHER notes (the entities in `affects:`); the decision note itself records the decision, not its propagation.

## Sources

This V1.1 schema deepening derived from:

- Operator instruction, 2026-05-17 Claude Code session (desk-setup project, decision-schema deepening pass following the meeting-schema deepening)
- Three instances created 2026-05-17 during the meeting-matching pass — the dogfood that surfaced the patterns codified here:
  - `../../atlas/decisions/2026-05-15-acme-consulting-direction-reframe`
  - `../../atlas/decisions/2026-05-15-acme-consulting-team-restructuring-30-60-90`
  - `../../atlas/decisions/2026-05-15-coe-workshops-as-qualification-funnel`
- Legacy decision notes sampled from the operator's prior vault — surfaced the optional "People Involved" and "Team Memo" body sections, plus the `business:` frontmatter scoping pattern
- V1.0 stub of this file (2026-05-09 era) — preserved core body sections and matching clause; V1.1 extends with lifecycle table, standalone-worthy criteria, creation workflow paths, confidentiality conventions, and Creating-an-instance workflow
- [[type-scaffolding]] — schema design discipline
- [[instance-scaffolding]] — conditional-matching pattern
- [[matching]] — cross-update discipline
- [[../../config/objects/meeting]] V1.1 — sibling schema; decision V1.1 mirrors meeting's structure where the patterns translate
