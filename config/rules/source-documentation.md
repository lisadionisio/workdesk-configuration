# Source Documentation

Every note in the knowledge layer must trace to a source. The reader must always be able to answer: where did this come from?

## When this applies

- Creating any new note in `atlas/` (meetings, people, companies, projects, decisions, content)
- Creating any new note in `intel/` (reads, concepts, reference, briefings)
- Creating session logs in `system/session-log/`
- Updating an existing note with new claims or information
- Processing any external input into the vault

## What to do

- **Atlas notes** must trace to a real interaction. Meeting notes link to transcripts via the `transcript:` frontmatter field. Person notes cite the meetings or conversations that informed them. Decision notes link to the discussion that produced the decision. If a claim in an Atlas note cannot be traced to a specific interaction, it does not belong in Atlas.
- **Client and project `_status.md` files are Atlas notes too — they carry a `## Sources` section.** They are not exempt. A status file asserts current-state facts about a client — phase, fee, contacts, open items — and those facts came from somewhere; without sourcing, a reader cannot tell whether a line came from a kickoff call, from Financial Cents, or from an assumption. Collect the interactions the file already cites inline into a `## Sources` section at the bottom. **Where a fact's origin is genuinely unrecorded, say so — do not backfill attribution.** Facts read live from Financial Cents, QuickBooks, or the shared Drive are system reads, not vault notes; name the system rather than forcing a wikilink. *(Operator decision, 2026-09-04 — settled as a class after four client status files were found carrying no provenance at all.)*
- **Intel notes** must be explicitly labeled as agent analysis. Name the actual generating agent or model only when verified; never attribute another runtime's work to Claude by default. Intel is agent-generated synthesis — useful but lower trust. The frontmatter `type:` field and the `intel/` location provide structural labeling. When an Intel note builds on Atlas sources, link to them. When it's purely synthesized, say so.
- **Intel research from external sources** must cite what was read. Include URLs, publication dates where available, and date accessed. For notes synthesized from multiple web sources, list them in a "Sources" section at the bottom of the note.
- **Live session sources** are valid. When the operator provides information or makes a decision directly in an agent session, the source is "Operator instruction, [date]". If `/extract` is run at the end of the session, link to the session log in `system/session-log/`. Live sessions are real interactions — treat them like any other Atlas source.
- **Inline attribution** for specific claims: "Per the March 12 meeting with [[martin-holland]]..." or "Based on `2026-03-12-cfc-running-on-numbers`...". Not every sentence needs a citation, but every non-obvious claim needs one.
- When updating a note with new information, attribute the new information to its source. Don't blend new facts into old text without marking where they came from.

## What NOT to do

- Do not create an Atlas note that cannot answer "where did this come from?" with a specific source. If the source is "the agent inferred this," it belongs in Intel, not Atlas.
- Do not strip source references when editing or condensing a note. Provenance survives every edit.
- Do not cite notes in `system/_processing/_done/` as sources — link to the processed meeting note in `atlas/meetings/`, which in turn links to its transcript.
- Do not fabricate attribution. If you don't know the source, say so explicitly rather than guessing.
