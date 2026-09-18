# Matching — Context Belongs Together

When processing any input, every related note must be updated in the same pass. A meeting about a project updates the project status. An action item from a client call appears on both the meeting note and the company note. Related information must be connected at the point of creation, not in a separate pass later.

## When this applies

- Processing a meeting transcript into vault notes
- Processing communications (Slack, email) into vault notes
- Logging a decision from any source
- Updating a person, company, or project note with new information
- Routing action items from any interaction

## What to do

- After processing any interaction, identify all entities touched: people, companies, projects, decisions. Update each entity's note **only when there is substantive new information** — decisions made, action items assigned, relationship changes, new context that changes understanding. Passing mentions ("Sarah was also on the call") do not trigger updates unless they add meaningful context.
- Action items live inline on the notes they belong to. A client action item goes on the company note with a source link to the meeting that produced it. No standalone action-item files. (This bars a parallel *tracking list* of actions duplicated off their source note — it does NOT bar the operator's own `gtd/inbox/[ACTION]` captures, which are the GTD in-tray for the operator's own commitments per [[gtd-inbox-processing]], not a duplicate record of someone else's delegated work. Delegated/client actions stay inline only; only the operator's own actions become inbox items.)
- When a decision is logged to `atlas/decisions/`, it links back to the meeting or conversation that produced it AND forward to the project it affects. Three-way matching: source, decision record, project status.
- When processing a meeting that mentions a project, update the project's `_status.md` with relevant outcomes. Don't leave it for a separate status update pass.
- Before finishing a processing run, verify: did every entity with substantive new information get its note touched? The test is not "was this entity mentioned?" but "did this interaction produce new knowledge about this entity?"

## Scoped execution (Night Shift, build production)

During scoped execution, matching applies to notes within the spec's scope. If a processing run surfaces substantive information about an entity outside the spec's scope (e.g., Night Shift discovers Project Y needs a status update while building for Project X), log the needed update as a finding for the orchestrator or the morning review — do not cross-update outside your assigned scope. Scope discipline takes precedence over matching completeness during scoped work.

## Parallel source processing (fan-out)

When a single processing run fans out across **multiple workers in parallel** (e.g. a large transcript backlog split into entity-clusters), "same pass" means "same orchestrated run," NOT "each worker writes every related note." There is no file locking — two workers editing the same note is last-writer-wins clobbering.

- A worker may directly write only files **uniquely owned by its scope** (its own primary notes, uniquely-named inbox items, its own source archival).
- Any **shared** note — client/business/project `_status.md` or `_brief.md`, or a note any other worker could also touch — is off-limits during fan-out. The worker defers the update as a structured **finding** (durable file, not chat), exactly as scoped execution does.
- A **single writer** consolidates all findings sequentially before the run is complete — applying them chronologically per target file (see § Conflicting information), idempotently, with source attribution.
- Direct concurrent writes to shared entity notes are forbidden. When in doubt about whether a note is shared, treat it as shared and defer.

This is the general constraint; the transcript processor's "Parallel backlog mode" is one enforcement point of it.

## Conflicting information

When multiple sources in the same processing batch conflict on the same entity:
1. Process meetings in chronological order
2. Document BOTH positions on the entity note with source attribution ("Per `meeting-1`, Tiger said X. Per `meeting-2`, Sarah said Y.")
3. Use the most recent chronological source as the working state, but mark confidence:
   - **Confirmed** (decision-maker stated directly) vs **Reported** (secondhand)
   - **Final** vs **Conditional** ("pending confirmation")
4. Create a `[QUESTION]` inbox item: "Conflicting information about [entity]: [summary]. Which is current?"
5. Do NOT delete or overwrite the earlier information — both positions remain until the operator resolves

## Inbox notifications

When processing produces outputs that need operator review, create inbox notifications following these rules:
- **Inbox is a notification layer, not storage.** The actual note lives in its permanent location (atlas/, intel/). The inbox item is a lightweight pointer with a wikilink to the real note.
- **Only create inbox notifications for outputs that need operator attention.** Trusted, recurring processes (like meeting note updates to existing company notes) run silently. New note types, new entities, content candidates, and flagged issues get inbox notifications.
- **Use the correct prefix:** `[ACTION]`, `[REVIEW]`, `[CONTENT]`, `[QUESTION]`, `[AWARENESS]`.
- **Use the locked inbox-item format** — every inbox item opens with an `## Operator review` H2 section + single empty bullet + horizontal rule, directly under the H1 title. See [[inbox-item-format]] for the exact shape.
- **Never duplicate content between inbox and the real note.** The inbox item has a one-line summary, key points, and a link. The real note has the full content.

## What NOT to do

- Do not process a meeting note in isolation. If the meeting produced substantive new information about Project X, Person Y, and Company Z, all three notes must reflect it — not just the meeting note.
- Do not create action items as standalone files. Actions belong on the note they relate to (company note, project note) with a source link to where they originated.
- Do not defer cross-updates to a later pass. "I'll update the project status later" is how matching breaks down. Do it now.
- Do not update an entity note without linking back to the source of the new information. Every update is traceable.
