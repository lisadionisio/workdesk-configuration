# Contributing to WorkDesk OS

WorkDesk OS ships configuration (skills, rules, scripts, hooks) to operator vaults via
tagged releases and the `/update` skill. Changes merged here reach every operator's vault,
so the bar is: CI green, conventions followed, downstream impact stated.

## Reporting a problem

Use the `/feedback` skill from your vault — it files a GitHub issue with diagnostics and
never includes your vault content. That's the right path for "something is broken" even if
you're not sure where.

## Sending a fix or improvement

1. **Fork the repo** (external contributors) or branch from `main` (collaborators).
2. **Branch naming**: `fix/<slug>` or `feat/<slug>` for hand-written work;
   agent-authored work uses `claude/<task>` or `codex/<task>`.
3. **Keep operator-personal values out of shipped files.** Nothing under `config/` may
   contain a real name, email, absolute user path, or UUID. CI runs
   `tests/genericity-check.sh` on every PR and will fail the build; run it locally first.
4. **Run the tests you touched**, and at minimum `./tests/smoke.sh`. CI runs shellcheck,
   the settings schema check, the smoke suite, and the update-engine matrix
   (`tests/migrate-test.sh`) on every PR.
5. **Open the PR against `main`** using the pull request template. The
   `## Downstream impact` section is required whenever the change touches
   `config/operator-profile.md`, any frontmatter shape, or directory structure —
   explain what happens to existing vaults and whether a migration is needed.

Merges to `main` require green CI (branch protection). Releases are cut by the repo
owner as needed; your merged change ships in the next release and reaches operators
when they run `/update`.

## If your fix lives in your vault

If you patched a shipped script or skill inside your own vault, upstream it — vault-local
fixes to shipped files are overwritten when a future release changes the same file. Copy
the change into a fork branch as above. If you can't fully test outside your vault, say so
in the PR's Test plan; maintainer CI covers the rest.

## What not to do

- Don't include real operator data in examples — use `alex@example.com`-style identities.
- Don't add a `CHANGELOG.md`; release notes are generated at release time.
- Don't edit `config/VERSION`; the release process owns it.
- Don't add migrations under `config/scripts/`; they live at repo-root `migrations/`.

## Migration recovery and concurrency

Migrations must be idempotent. Version-named migrations run only when their destination version is newer than the installed version and no newer than the staged release. A successful migration has a hash receipt within staging so an interrupted attempt can resume. An interrupted migration without a success receipt may be rerun and must preserve operator data. Existing versioned migrations include their own precondition/no-op checks.

`check` saves the reviewed target hashes inside staging. `apply` reclassifies current ownership and checks the reviewed hash immediately before each write; it cannot silently accept a later edit through an old resolution. A manually prepared staging tree should include a reviewed-plan.json with these hashes; without it, legacy callers receive only apply-time race checks. Symlink targets require manual reconciliation before writes. Failed partial applies preserve snapshots and current files, retain the old VERSION, and require per-file recovery; do not blindly restore the full directory over newer edits.
