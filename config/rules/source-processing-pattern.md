# Source Processing Pattern

Every substantive processed source produces one primary synthesis note. A source with no usable content receives an explicit no-content disposition and source-grounded reason, preserving the raw source without inventing a synthesis, meeting or commitment. From a substantive primary, secondary artifacts are extracted as the source's content warrants. Inbox notifications fire only when the synthesis identifies an application or contradiction worth operator attention. This pattern applies to every source-kind — bookmarks, transcripts, session-logs, future kinds — so processing stays consistent across the vault.

## When this applies

- Any time a source's processing rule executes (operator-invoked, dispatcher-invoked, or scheduled)
- When defining a new source-kind via `/define-source` — the new source's processing rule must conform to this pattern
- When reviewing or editing an existing source-kind's processing rule

## The pattern

```
1 substantive source → 1 primary synthesis note (location depends on kind)
              + 0..n quotes                     (gtd/reference/quotes/)
              + 0..n concepts                   (intel/concepts/)
              + 0..n atlas-object updates       (atlas/people/, atlas/decisions/, atlas/meetings/)
              + 0..n action items               (gtd/inbox/ with [ACTION])
              + 0..1 review notification        (gtd/inbox/ with [REVIEW])
```

The synthesis is the coherent unit the operator or future agent sessions will revisit. Secondaries are referenced from it. Notifications fire only when the synthesis warrants operator review. A verified no-content disposition is the exception to synthesis creation, not permission to skip substantive sources.

## Step-by-step

### 1. Vault-aware reading (before forming the synthesis)

Read enough vault context to judge applicability and detect contradictions:

- `gtd/projects/*/_status.md` and `_brief.md` — what the operator is actively working on
- `personal/` — operator-owned practices, journals, current thinking (read-only; Claude never writes here)
- `config/operator-profile.md` — identity, businesses, areas of work, tools in use
- Recent `intel/syntheses/` and `intel/observations/` — last 14 days, so Claude doesn't repeat itself
- `atlas/decisions/` — relevant decisions that the source might confirm or contradict

Without this context, the synthesis is generic summarization — useless to the operator.

### 2. Produce the primary synthesis

Location depends on source-kind:

| Source kind | Synthesis location | Reason |
|---|---|---|
| External content the operator consumed (bookmark, article, video) | `intel/syntheses/{date}-{slug}.md` | Agent synthesis belongs in intel; attribute it to the actual authoring runtime. |
| Interaction the operator was in (transcript, meeting, call) | `atlas/meetings/{date}-{slug}.md` | The meeting note IS the synthesis for a meeting. |
| Operator's own session/work (session-log, journal) | `system/session-log/{slug}.md` | Operator-shaped; lives where the raw was captured. |

The synthesis is a single note that:
- States what the source is, in one sentence
- Identifies whether it applies to anything in the vault — and if so, how, specifically
- Captures an operator takeaway only when the source or operator supplied it; distinguish the agent's interpretation from the operator's view
- Notes any contradictions with existing vault content, with the actual agent's analysis labeled as analysis
- Links back to the source via `[[source-slug]]`
- Lists any secondaries produced by this processing pass

### 3. Extract secondaries

Only extract what the source actually contains. Don't run every extraction path on every source.

**Honor source-kind exclusions.** Each source seed MAY declare an `excludes-secondaries:` list in its frontmatter. The processing rule MUST skip extraction of any secondary type listed there, regardless of content. The primary synthesis is never excluded — only secondaries.

```yaml
excludes-secondaries:
  - atlas/*           # wildcard — exclude all atlas types
  - atlas/people      # specific atlas type
  - quotes            # exclude quote extraction
  - concepts          # exclude concept extraction
  - actions           # exclude action-item extraction
```

Wildcards are supported with `*` at the end of a path segment. `atlas/*` excludes every `atlas/<type>` extraction (people, decisions, meetings, etc.). Specific types listed alongside wildcards are redundant but harmless. Different source-kinds suit different secondary types — bookmarks are operator-curated reading material, transcripts are interactions where atlas extractions are expected. Without this field, per-source constraints have to be remembered across sessions, which doesn't survive.

**Secondary types and their extraction rules:**

- **Quote** → `gtd/reference/quotes/{slug}.md` when the source contains a stand-alone line worth keeping with attribution.
- **Concept** → `intel/concepts/{slug}.md` when the source introduces a named, reusable framework or pattern (has identity, can be invoked by name).
- **Atlas updates** — apply [[matching]]: every entity touched (person, project, decision) gets its note updated when there's substantive new info. Pure mentions don't trigger updates.
- **Action item** → `gtd/inbox/` with `[ACTION]` prefix when the source contains a concrete to-do for the operator.

If a concept could become a project (compelling enough to spin its own work), still create the concept normally AND add a separate `[REVIEW]` inbox note: *"Concept `[[X]]` could be a project — consider scoping."* Don't invent a new outcome type.

### 4. Cross-link primary ↔ secondaries ↔ source

Per [[source-documentation]] and [[double-entry-knowledge]]:

- Primary synthesis links **back** to source: `[[source-slug]]`
- Primary synthesis lists secondaries via wikilinks: `Quotes: [[quote-slug]]`, etc.
- Each secondary links back to the primary synthesis (not directly to the source — the synthesis is the canonical context).
- Source frontmatter `processed-into:` populates with wikilinks to all produced notes.

### 5. Inbox notification — fire only when justified

Surface a `gtd/inbox/` `[REVIEW]` notification when ANY of these are true:

- **Application identified.** The synthesis names a current project, area, business, or active question the source applies to.
- **Contradiction detected.** The source contradicts a decision in `atlas/decisions/`, a stated belief in operator-profile, or an established pattern in operator's work.
- **Project candidate.** A concept produced from this source could plausibly be its own project.

Otherwise, no notification. Pure quotes, generic-interest synthesis, "doesn't apply right now" — silent. The synthesis still exists in `intel/syntheses/`; the operator can find it via search if they need it.

### 6. Contradiction handling

When a source contradicts existing vault content:

- Flag the contradiction explicitly in the synthesis: *"This conflicts with `[[decision-Y]]` from `[[meeting-Z]]`."*
- Give the authoring agent's analysis, labeled separately from source facts. Use research where appropriate and cite sources.
- Do NOT silently overwrite or ignore the prior content. The contradiction is information.
- Surface a `[REVIEW]` inbox notification — contradictions always warrant operator attention.

### 7. Apply matching and source-documentation

Before closing the processing pass:

- [[matching]]: every entity with substantive new info gets its note updated in this same pass. Don't defer.
- [[source-documentation]]: every produced note traces to the source via wikilink chain. Provenance preserved.
- Parse every required machine-readable receipt or manifest with its actual format parser before claiming completion. For JSON, require the entire file to parse as the requested object; trailing markup or a parseable prefix is not valid. Inspect the final response against the same sources as the stored notes: a correct note does not excuse an unsupported claim in the handoff.

When processing fans out across parallel workers (see [[matching]] § Parallel source processing), shared-note updates are deferred as durable findings and applied by a single consolidation writer — but the run is not complete, and a source is not archived, until consolidation and verification have finished. A source landing in its archive folder is not by itself proof of completion.

### 8. Verify and finalize source completion

After all required artifacts are produced, cross-linked and verified:

- Use the source-kind's supported completion procedure. Transcripts use the helper specified by the transcript workflow, including its source snapshot and receipt verification; do not hand-author a substitute completion receipt.
- Preserve source identity and original bytes. Archive without overwriting an existing source, then verify output links and source references at their final locations before finalizing `processed: true` and `processed-into:`. Revalidate the resulting completion record and current files. An interrupted archive or a true flag alone is not proof of completion.
- For a no-content source, record the reviewed disposition and reason through the supported procedure without creating downstream knowledge notes.
- If the source-kind requires external processed-state synchronization, verify that operation too. A failure leaves that required substep pending and prevents an overall success claim; retain verified local outputs and resume the missing substep rather than recreating them.

## What NOT to do

- Don't extract every possible artifact type on every source. Extract only what content warrants.
- Don't skip the synthesis for a substantive source. Even when no application is identified, retain "no clear application" with its source link. Truly contentless sources use the explicit no-content disposition instead.
- Don't fire a `[REVIEW]` notification just because something was processed. Notifications are for application/contradiction/project-candidate signals only. Routine processing runs silently.
- Don't write to `personal/`. Vault-aware reading includes `personal/`, but writes never go there. If a source produces personal-shaped content, route to `gtd/inbox/` with `[REVIEW]` and let the operator move it themselves.
- Don't bypass [[matching]] or [[source-documentation]]. Every entity gets its update; every produced note traces to its source.
- Don't fabricate context to force an application. If the source doesn't apply to anything, the synthesis says so. Per [[no-fabrication]].
