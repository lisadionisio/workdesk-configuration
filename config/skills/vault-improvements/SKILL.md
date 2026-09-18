---
name: vault-improvements
description: Run the declared weekly vault improvement review or inspect reported drift; produce sourced proposals without applying structural changes.
---

# Vault improvements

Read `config/signals/vault-improvements.md`, relevant rules, and this skill's learnings if present. Resolve these paths from the vault root, not the global skill installation directory.

1. Read the declaration's sources within the stated lookback. Record each source as available, empty, unavailable or stale. File modification time does not establish that the operator read a note. If read-state is unavailable, say so.
2. Run `bash config/scripts/check-wikilinks.sh` on a bounded sample of current notes. Historical records remain valid link targets. A broken-link finding needs a target check before proposing a repair; do not bulk-rewrite links.
3. Review overdue projects across `gtd/projects/`, `atlas/businesses/*/projects/`, and `atlas/clients/*/projects/`. Review repeated failures/corrections, unresolved questions and available outcome records. Age alone neither closes a project nor resolves a question.
4. Write the declaration's dated report with source links, as-of time, coverage gaps, severity, proposed action and owner. Distinguish observation from inference. Do not modify personal notes or implement new schemas, tools, rules or cleanup as a side effect.
5. Create only necessary, deduplicated review pointers in the established inbox format. Check for an existing report/finding first. Preserve concurrent edits.
6. Verify the report and its links. Update this signal's `last-fired` only after a usable report exists; record partial coverage in the report. A failed required step remains failed with a next action, and must not advance completion state.

A weekly due signal is an invitation to run this review, not authorization to change configuration. Apply accepted improvements separately with targeted tests and the release/ownership contract.
