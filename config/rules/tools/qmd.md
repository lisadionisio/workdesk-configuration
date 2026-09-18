---
paths:
  - "**/*.md"
---

# QMD — Tool Reference

Hybrid semantic and keyword search over vault markdown files. Use for finding conceptually related notes, not just exact text matches.

## Access Method

CLI command: `qmd`. Resolve it with `command -v qmd` in the actual agent or job environment; installation paths differ by host. An executable being present does not establish that the intended vault is indexed.

Select the reviewed Workdesk index explicitly on every command, including retrieval of a result. The examples below use a separately configured named index, `workdesk-os`. Confirm that its configuration exists and points at this operating vault before use. Do not infer that the default index belongs to Workdesk: another vault may already own it. Provision and verify the named index before adopting these commands; an absent named index is unavailable coverage, not permission to repoint an existing index. If the installation uses another reviewed name, use that name consistently.

## Common Commands

| Command | What it does | Example |
|---|---|---|
| `qmd --index workdesk-os query "topic"` | Semantic + keyword hybrid search | `qmd --index workdesk-os query "client onboarding process"` |
| `qmd --index workdesk-os status` | Check index health and status | `qmd --index workdesk-os status` |
| `qmd --index workdesk-os collection list` | Check configured scope | `qmd --index workdesk-os collection list` |
| `qmd --index workdesk-os search "topic" --json` | Keyword search without a model | `qmd --index workdesk-os search "ownership contract" --json` |
| `qmd --index workdesk-os multi-get <reference> --json` | Read indexed content as JSON | `qmd --index workdesk-os multi-get qmd://workdesk/path/to/note.md --json` |

## Readiness and source verification

- Confirm the intended vault path and markdown scope in the collection configuration. An empty collection list is unavailable search coverage, not evidence that the vault contains no relevant notes.
- Check indexed document counts, pending embeddings and the last update. Keyword indexing and embedding completion are separate checks. Do not describe partial embedding coverage as complete semantic search.
- `qmd update` refreshes configured collections; `qmd embed` generates pending embeddings. A periodic runner must have an explicit owner and verified environment. Do not add `--pull` to an index refresh: vault transport is managed separately.
- Before running or scheduling `qmd update`, review every collection in that index. In QMD 2.0.1, a collection's configured `update` shell command runs automatically even without `--pull`. Require no such commands for an index-only refresh, or review and authorize their effects separately. A shared index may contain collections outside the intended vault.
- Results and retrieved content are cached index snapshots. Verify the current source file before citing its current state or editing it. QMD virtual paths can normalize case, spaces and punctuation; do not turn them directly into filesystem paths or Obsidian links without resolving the actual filename.
- Search ranking does not establish source authority. Distinguish raw sources, sourced records, agent analysis and historical notes when using results.

## Known Limitations

The optional `config/scripts/refresh-search.py` runner requires the pinned source-processing Python runtime with PyYAML 6.0.3, explicit absolute Node/QMD/package paths, an existing host-local index, a host-local state directory, the reviewed configuration SHA-256, and a positive `--minimum-source-files` floor. Use its `--help` for required arguments. Its companion `verify-search-index.mjs` must be installed beside it. The adapter currently supports QMD 2.0.1, including its optional hexadecimal Git suffix; revalidate before changing that version.

The runner verifies that the QMD executable resolves to the declared package's `bin/qmd`, then invokes that package's CLI entry point with the explicit Node runtime. The verifier uses that same resolved runtime and package. This avoids the shell shim choosing another Node or Bun through PATH. Verify native-module compatibility with that Node before installation; matching the QMD version alone is insufficient. The runner freezes the explicitly supplied configuration under its private run directory and addresses the SQLite index by absolute path, independently of interactive named-index defaults.

Record the embedding model file's SHA-256 with initial index and evaluation evidence. The same model alias or cache filename can contain different bytes on two hosts; do not transfer vectors between them or claim equivalent retrieval without checking model identity. Prefer independently verified host-local indexes when the model files differ. The full verifier uses QMD's own SQLite library and tokenizer, and awaits the library's model cleanup before exit. A passing JSON report followed by a nonzero or aborted process is still a failed verification.

Choose the source-count floor from a verified, fully available vault inventory and record the permitted margin when installing the job. Count non-empty Markdown files within the reviewed QMD scope, not every filesystem entry. The runner reads that scope before any update and refuses a count below the fixed floor. An empty folder, an incomplete Sync copy or a changing inventory cannot replace the last-success receipt. Never lower the floor automatically after a failure; first restore source availability or review an intentional change in scope. A recovered inventory may retry without an index-repair override because the refused attempt did not start update/embed. The full post-update check also enforces the floor. These are observations before and after indexing, not an atomic filesystem snapshot; source changes between them remain possible.

Before scheduling, drain other QMD writers, verify the initial index, and route subsequent refreshes through the same runner lock. A completed receipt includes source and full-chunk checks for that observation; it is not a promise that no later edits occurred. Source-only staleness can be retried by a later refresh. Partial embedding, invalid verifier output or an interrupted run requires independent repair/reconciliation. Do not delete a repair marker merely to make a retry run. Ordinary refreshes never clear repair markers. Preserve the last successful receipt when a newer run fails.

### Recover a blocked refresh

1. Confirm all index writers have stopped, including manual QMD commands outside the runner lock. Read `last-run.json`, its run logs, and `repair-required.json` when present. Establish whether the failure reflects missing/corrupt vectors, changed source availability, or inability to finish verification. Repair actual source/index defects through a separately reviewed procedure; reconciliation does not repair or re-embed anything.
2. Record the SHA-256 of the exact `last-run.json` you reviewed. Reuse the installed job's explicit vault, configuration/hash, index, state, executable/package and source-floor arguments, adding `--reconcile-reviewed-receipt-sha256 <reviewed-hash>`. Do not add this option to a recurring job or lower the source floor to make recovery pass.
3. Reconciliation acquires the same lock, preserves the prior receipt and marker in its run directory, then runs source inventory and full verification without update/embed. A failed attempt leaves the existing blocking records and last-success receipt intact; inspect the returned attempt's `run` path for its failure evidence. If state changes during verification, the attempt stops without overwriting that newer state.
4. Only a successful full verification may retire the marker into that run directory and write a completed receipt with `mode: reconcile`. The prior failure remains available alongside it. Confirm exit 0 and the completed receipt before resuming normal refreshes. A reconciliation receipt proves observed coverage, not that a new indexing run occurred. An interruption while recording recovery may leave a conservative block; inspect the preserved records and perform a newly reviewed reconciliation instead of deleting state files.

Verifier stdout must be complete JSON; its stderr is preserved in a separate receipt-linked log. A source file disappearing during verification is a source-change failure; a document whose indexed content is missing is index corruption. Neither result is successful coverage. Repeated failures remain actionable even when retry is permitted. Current recovery is conservative for interrupted or incomplete verification: inspect both `repair-required.json` and `last-run.json` before reconciliation, since either can prevent a new run.

- Results depend on index freshness — newly created notes may not appear until the index updates.
- Semantic search can surface conceptually related but not literally matching notes — verify relevance before acting on results.
- First-use model downloads can mix progress into requested JSON output. Parse the complete response and reject non-JSON output; finish model provisioning separately and retry the read. Do not silently strip arbitrary text to accept an agent receipt.
- In QMD 2.0.1, `get` returns a text document even when passed `--json`; use the documented `multi-get --json` interface for structured retrieval. Verify supported options when changing versions.

## Common Mistakes

- Using QMD when you need exact text matching — use Grep instead for literal string searches.
- Not running `qmd status` when queries return unexpected results — the index may be stale or unhealthy.
- Using overly long queries — short, focused topic phrases work best for semantic search.

## Authentication

None required. Reads directly from the vault's markdown files.
