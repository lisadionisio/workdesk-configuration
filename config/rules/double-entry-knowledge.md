# Double-Entry Knowledge

Every note must have both content and connections. A note without links is single-entry — it records what happened but not where it came from or what it relates to.

## When this applies

- Creating any new note in `atlas/`, `intel/`, or `system/session-log/`
- Processing a transcript, meeting, or communication into vault notes
- Updating an existing note with new information
- Routing captures from the daily note to permanent locations

## What to do

- Every mention of a person, company, project, meeting, or decision that has its own note MUST use a `[[wikilink]]`. Scan for plain-text references before finishing any note.
- Before creating a wikilink, verify the target note exists. If it doesn't exist and you have the full information to create it, create it. If you only have partial information (first name only, company without context), use plain text — wikilinks are assertions of existence, not guesses.
- When finishing a note, run this checklist:
  1. Are all entities (people, companies, projects) linked?
  2. Does the note link to related notes that provide context?
  3. Would someone reading only the graph connections understand what this note relates to?
  4. Run `bash config/scripts/check-wikilinks.sh --require-links <created-or-updated-knowledge-notes>`. Zero scanned references is a failure for a knowledge note, not a passing connection check. Check raw sources separately without this option; do not fabricate a link to make the check pass.
- Source provenance (where the knowledge came from) is enforced by the Source Documentation rule, not this one. This rule enforces connections between notes.

## What NOT to do

- Do not create a note with zero outgoing wikilinks. If a note genuinely connects to nothing, question whether it belongs in the vault. Exception: `intel/reference/` notes may have minimal connections — a link to the requesting project or spec is sufficient. Reference notes are often self-contained by nature.
- When processing multiple entities, batch-verify existence at the start of the processing run (single Glob for `atlas/people/*`, `atlas/companies/*`, etc.) rather than checking one at a time. This keeps verification fast even when a meeting mentions 10+ entities.
- Do not use plain text for entities that have existing notes. "Martin" when `[[martin-holland]]` exists is a broken connection.
- Do not create wikilinks to notes that don't exist just to satisfy this rule. A gap is better than a broken link.
- Do not confuse tags with connections. Tags are for filtering. Wikilinks are the primary connection mechanism.
