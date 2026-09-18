---
name: update
description: Pulls the latest WorkDesk OS configuration release (skills, rules, scripts, hooks, schema) and applies it to this vault. Does NOT update the WorkDesk desktop plugin/app — that's installed and updated through a separate channel. Walks you through any conflicts in plain language. Operator data (personal/, atlas/, gtd/, intel/, system/) is never touched.
---

# /update

Updates the WorkDesk OS **configuration** (`config/` — skills, rules, scripts, hooks, schema) to the latest release. Operator-facing skill — most users won't be technical, so Claude leads with plain-language narration. The bash engine (`config/scripts/migrate.sh`) owns invariants; this skill orchestrates the conversation.

> [!info] What this updates vs. what it doesn't
> **This skill updates the configuration only** — the agent-side rules, skills, hooks, and scripts that live inside the vault's `config/` directory. Source: `BenaliHQ/workdesk-configuration` releases.
>
> **This skill does NOT update the WorkDesk desktop plugin/app.** That's a separate Obsidian plugin (source: `BenaliHQ/workdesk-operating-system`) installed and updated through its own channel — not through `/update`.

## Boundaries

- **Updates only `config/`** — your skills, rules, hooks, scripts, and the operator-profile schema. Never touches `personal/`, `atlas/`, `gtd/`, `intel/`, or `system/`.
- **Does not touch the WorkDesk desktop plugin.** The plugin (`BenaliHQ/workdesk-operating-system`) is a separate install with its own update channel. If the operator asks about plugin updates, redirect them — `/update` cannot help.
- **Schema migrations** may rewrite specific files inside `config/` (e.g. `operator-profile.md` frontmatter when fields are renamed). Each migration is a versioned, reviewable script shipped with the release.
- **Backup is automatic** before any write, at `<vault>/.workdesk-backups/<timestamp>/`. Restore via `/update restore <id>` or by running `config/scripts/migrate.sh restore <id>`.

## Phases

### 1. Check

Run:

```
config/scripts/migrate.sh check
```

This fetches the latest release from GitHub, verifies its SHA256, extracts it to a staging directory, and prints a plan as JSON to stdout.

Parse the JSON. Three top-level cases:

- `status: "up-to-date"` — tell the operator: "You're on the latest release (vX). Nothing to update." Done.
- `status: "update-available"` — continue to phase 2.
- Engine error — surface it plainly. Common causes: no network, GitHub rate limit, release tooling broken on the repo side.

### 2. Narrate the plan

The plan JSON contains:

```
{
  "current_version": "1.2.0",
  "new_version":     "1.3.0",
  "staging":         "/path/to/extracted",
  "migrations":      ["1.2.0-to-1.3.0-foo.sh"],
  "files": {
    "skills/daily-ops/SKILL.md": {"action": "clean-update"},
    ...
  }
}
```

Action values and their meaning:

| Action | What happened | Operator-facing summary |
|---|---|---|
| `no-op` | File matches new release already, or operator's edits don't conflict | (don't mention) |
| `clean-update` | New release changed this file; operator hadn't edited it | "Updated cleanly" |
| `add` | New file in the release | "New: <path>" |
| `conflict` | Both operator and release changed the same file | Walk one at a time in phase 3 |
| `removed-in-release` | Release removes this file; operator's copy stays | "No longer shipped: <path>" |
| `operator-deleted-changed` | Operator deleted; release changed | Treat as conflict in phase 3 |
| `operator-deleted-removed` | Operator deleted; release removed | (don't mention) |
| `manual` | Target is a symlink; reconcile its ownership and destination before any apply | "This linked file needs a manual ownership check" |
| `operator-only` | Operator's custom file, not in release | (don't mention; preserved) |

Summarize like this — short, factual, one paragraph:

> WorkDesk OS **configuration** v1.3.0 is available. This update modifies 4 skills, adds 1 new skill, and ships 1 schema migration (operator-profile field rename). 2 files have conflicts because you've customized them. Want to proceed?

(Lead with the word "configuration" so the operator doesn't confuse this with the desktop plugin.)

Wait for a yes/no. No preamble dump of every file path. If they say no, exit cleanly.

### 3. Walk conflicts (one at a time)

For each `conflict` (and `operator-deleted-changed`):

1. Read the operator's current file: `config/<path>`
2. Read the release version: `<staging>/workdesk/<path>`
3. Read the prior baseline (what the operator started from): `config/defaults/<path>`
4. Diff in your head: what did the operator change, what did the release change, do they overlap?
5. Tell the operator in plain language. Example:
   > **`skills/daily-ops/SKILL.md`** — You customized the evening-review section to add a reading log. The new version changes the morning section to add a calendar check. These don't actually overlap. Three options:
   > 1. Keep yours (the new morning improvement is skipped)
   > 2. Take the update (your evening customization is archived)
   > 3. Let me merge them — I'll combine your evening edits with the new morning section and show you the result first
6. Wait for one of: `1`, `2`, `3`, `keep`, `take`, `merge` (or natural language). One question per turn.
7. If they pick **merge**:
   - Construct the merged file in your head, applying both sets of changes
   - Write it to `<vault>/.workdesk-migrate-tmp/merged-<sanitized-path>`
   - Show the operator a tight summary: "I'll combine X from yours with Y from the new version. Diff vs your current: +12 lines, -3 lines. Approve?"
   - On approval, record `{"resolution": "merged", "merged_path": "<full path>"}` in resolutions
8. If they pick **keep** / **mine**: record `{"resolution": "mine"}`
9. If they pick **take** / **theirs**: record `{"resolution": "theirs"}`

Build the resolutions object as you go. After all conflicts resolved, write to `<vault>/.workdesk-migrate-tmp/resolutions.json`:

```json
{
  "skills/daily-ops/SKILL.md": {"resolution": "merged", "merged_path": "/path/to/.workdesk-migrate-tmp/merged-skills-daily-ops-SKILL.md"},
  "skills/pobo/SKILL.md": {"resolution": "mine"}
}
```

### 4. Apply

Run:

```
config/scripts/migrate.sh apply <staging> <vault>/.workdesk-migrate-tmp/resolutions.json
```

Both paths come from the plan JSON (`staging`) and what you just wrote.

Retain `reviewed-plan.json` in the staged package. Its target hashes bind resolutions to the reviewed files. Do not refresh a hash merely to force an old resolution through. If a target changed, preserve both versions and reconcile it first. A `manual` action stops apply before writes.

The engine:
1. Backs up `config/` to `<vault>/.workdesk-backups/<timestamp>/`
2. Applies each file per the plan + resolutions
3. Runs applicable schema migrations in order, skipping versioned migrations already covered by the installed version and recording completed migration hashes for an interrupted retry
4. Atomically swaps in the new `defaults/` snapshot
5. Bumps `VERSION` last
6. Prints a JSON result: `{"status":"applied","new_version":"1.3.0","backup_id":"2026-04-30-143022"}`

On failure, read the actual result. A partial apply preserves snapshots and current files instead of blindly restoring over concurrent edits; VERSION does not advance. Record the affected paths, preserve later edits, and reconcile a per-file recovery or reviewed retry. Never report a rollback that did not happen.

### 4b. Sync Obsidian defaults

After the engine `apply` succeeds, copy the canonical `.obsidian/` files from the new release into the operator's vault. These are settings the core Obsidian plugins (Daily Notes, Templater) read on launch — without them, daily-note creation lands at vault root with no template.

For each file under `<staging>/workdesk/obsidian-defaults/` (relative path preserved):

1. Compute target path: `<vault>/.obsidian/<relative-path>` (strip the `obsidian-defaults/` prefix).
2. If the target's parent directory doesn't exist, create it.
3. Overwrite the target with the source file contents. These files are canonical settings, not operator preferences — no merge, no prompt.
4. Skip the `README.md` in `obsidian-defaults/` — it's documentation, not config.

This applies on the **next natural Obsidian launch** — no toggle, no restart prompt. Don't ask the operator to do anything. The launch happens whenever they next reboot, update Obsidian, or close and reopen the app normally.

If the operator says they want the change to take effect immediately in their current Obsidian session, point them at: `Settings → Core plugins → toggle "Daily notes" off, then on`. That re-reads the config without an app restart. But this is a rare ask — for most operators, "next launch" is fine.

### 4c. Refresh the vault README

The vault-root `README.md` is materialized from `config/templates/vault-readme.md` at onboarding, but it lives outside `config/`, so the engine's apply step never refreshes it. This step keeps it current after each update — without clobbering an operator who hand-edited theirs.

The apply result JSON gives you `backup_id`. Three files matter:

- **prior template** — `<vault>/.workdesk-backups/<backup_id>/templates/vault-readme.md` (what the README was last generated from — the backup is a snapshot of `config/`, so the template is under it)
- **new template** — `<vault>/config/templates/vault-readme.md` (just applied)
- **current README** — `<vault>/README.md`

Decide in this order:

1. If the **new template equals the current README** → already current. Do nothing.
2. If the **current README is absent** → write it from the new template. (Same as onboarding's first-run behavior.)
3. If the **current README equals the prior template** → the operator never customized it. Snapshot it (below), then overwrite with the new template. This is the auto-replace path — silent, no prompt.
4. Otherwise (**current README differs from the prior template**) → the operator customized it. Do **not** overwrite. Tell them once: *"Your vault README differs from the shipped one, and there's an updated version (e.g. new tutorial videos). Want me to replace it? Your current README is backed up either way."* Replace only on an explicit yes; otherwise leave it untouched.

Before any overwrite (case 3, and case 4 on an explicit yes), snapshot the current README into the backup dir first, per [[no-silent-destruction]]:

```bash
cp "<vault>/README.md" "<vault>/.workdesk-backups/<backup_id>/README.md.vault-root"
```

If the prior-template baseline is missing (the operator last updated from a release older than this feature, so the backup has no `templates/vault-readme.md`), you can't tell customized from clean — treat it as case 4 and prompt rather than assuming.

### 5. Confirm and close

On success, tell the operator:

> Configuration updated to v1.3.0. Backed up your prior state to `.workdesk-backups/2026-04-30-143022/` (you can run `/update restore 2026-04-30-143022` to roll back). Restart Claude Code so the new skills load.

(Word "configuration" up front — keeps the desktop-plugin-vs-config distinction visible at the end of the flow.)

Tell them to restart Claude Code — skills are loaded at session start. Without a restart, the new skill bodies won't be picked up.

## Restore subcommand

If the operator says something like "undo that update" or "go back to before the update":

```
config/scripts/migrate.sh restore <backup-id>
```

`<backup-id>` is the timestamp directory name under `<vault>/.workdesk-backups/`. List them with `ls <vault>/.workdesk-backups/` if needed.

## Voice and pacing

- Plain language. No jargon. "Skills" and "rules" are fine; "control plane," "merge base," and "manifest" are not.
- One question per turn during conflict resolution.
- No status dump. Summarize the plan in one paragraph; mention specific files only when they need a decision.
- If the operator says no at any prompt, stop cleanly — no follow-up nag.

## Failure recovery

If the apply phase fails:
- Preserve the backup and current files; inspect whether any files were already applied.
- Tell the operator what actually changed, what failed, and the prepared recovery step. Do not claim an automatic rollback.
- Do not retry automatically.

If `check` fails (network, rate limit):
- Tell the operator the cause in one sentence
- Suggest waiting and re-running

A partially failed migration can leave changes even with the old VERSION. Surface the migration and affected files. Completed migration hashes are retained in staging; incomplete migrations must be safe to rerun. Never rerun obsolete global-state migrations merely because they are bundled in a newer release.

## Operator-private distribution

Use `migrate.sh private-overlay <package-dir>` to dry-run an enumerated private package, then append `--apply` within operator-authorized scope. The manifest records version and, per file, config-relative `target`, package-relative `source`, exact `ownership` (User config or User overrides of product), `before_sha256` (null for absent) and `after_sha256`. Ownership is checked against the existing product defaults. This mode never changes product VERSION/defaults, hooks, global paths or runtime state. It refuses changed targets, escaping paths, protected files and mismatched sources. Repeating an accepted package is a no-op.

Recovery is per file: `migrate.sh private-overlay <receipt.json> --restore-file <config-relative-path> --apply` checks that the installed file has not changed, snapshots it, then restores its verified previous bytes. Added files have no prior snapshot and require a reviewed disposition; there is no implicit deletion. Protected safety installation remains the separate operator-run allowlisted adapter.
