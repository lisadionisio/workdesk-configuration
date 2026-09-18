---
name: release
description: Owner-only Workdesk configuration release workflow for BenaliHQ/workdesk-configuration. Use for preparing, reviewing, publishing and verifying configuration releases; preserve operator data and coordinate through the repository session engine.
---

# Workdesk configuration release

Canonical maintained owner skill. This file is excluded from product payloads. Claude and Codex adapters should reference this one source, preserving runtime-specific tool names separately.

## Scope and paths

- Product repository: `BenaliHQ/workdesk-configuration`. Verify the actual remote before working; a checkout named workdesk-os may instead be an operator's private backup repository.
- Operating vault: `~/Workdesk-OS`, resolved on the current host. Its Git history is backup, not the product release source.
- `config/VERSION`, `scripts/release.sh`, `migrations/`, and `tests/` are relative to the verified product worktree.
- Use the operating vault's `config/scripts/repo-session.sh` with the verified repository path. Keep one private worktree per task; never edit or commit directly in a shared main checkout. Preserve other sessions.
- The Mac mini is the intended release coordinator. A shared canary is locked on the host that owns it; locks on two different hosts are not mutual exclusion. An isolated temporary canary owned solely by this task avoids cross-session collisions.

## Prepare the review

1. Verify repository identity and branch, inspect the existing project brief/status, and preserve a before-state. Use `repo-session.sh status` to check for competing work.
2. Classify the change and explain it: patch for a compatible fix, minor for additive capabilities, major for a breaking schema or interface change. Record the operator's confirmation or existing explicit authorization; otherwise include the recommended classification in the final review request. Do not ask a nontechnical operator to derive the version themselves.
3. Prepare the version bump in a reviewed branch as part of release preparation. Never commit a version bump directly to main. This reconciles the former direct-main instructions with the shared-repository contract.
4. Review file ownership: generic mechanisms may ship; private policy, credentials, client context and operator voice never enter public config. Classify operator overrides using the existing four-class contract. Release only deliberately reviewed files, never a bulk copy of an operating vault.
5. Run the relevant tests and at least smoke, migration matrix, genericity and lint checks. Add targeted fixtures for behavioral changes. Migrations must be idempotent, applicable to the prior version and limited to their declared scope. Inspect any legacy global-state migration before allowing it to run.
6. Open/update a PR with `## Summary`, `## Test plan`, and `## Downstream impact`. Include ownership, partial-failure handling, changed discovery paths, unsupported combinations and the rollback approach where relevant.
7. Use the session engine's finish/handoff flow when ready; it may close the worktree, so finish code changes and preserve evidence first. Find the PR by its returned URL or branch. Review the PR head and CI, rather than assuming a local feature checkout remains present.
8. CI must be green. Operator merge remains the default: **do not auto-merge without explicit operator authorization to perform that merge.** Prepare the complete review before asking. Do not force-push, bypass checks, amend published commits or touch another session's branch.

## Build, cut and verify

1. After merge, verify the exact reviewed commit is on remote main and includes the intended version. Use a clean isolated worktree for release commands; do not switch or pull a shared checkout with other work.
2. Run `scripts/release.sh --dry-run`. Inspect the archive and checksum. Verify private material, caches and runtime state are absent. Preserve the exact artifact and SHA for canary and publication.
3. Test an isolated canary installed from the actual prior release, with synthetic records in each operator zone and representative custom configuration. Do not fake a prior install by changing only VERSION. Snapshot it and record prior hashes.
4. For the transition to the revised migration engine, invoke the reviewed staged engine using `CLAUDE_PROJECT_DIR=<canary>` and its normal check/apply interfaces. Keep the engine's companion scripts beside it. An old live engine does not acquire the new conflict protections merely because a new payload was downloaded.
5. Reconcile conflicts deliberately. Verify version advancement, backup creation, customization preservation and unchanged operator-zone hashes. A second apply must leave managed configuration unchanged. Do not apply or restore broad Obsidian preferences as an unreviewed side effect.
6. Publish only with the operator's release authorization. After the built archive passes canary checks, use `scripts/release.sh --publish-built` (and `--notes-file` if needed), never a hand-written release creation that omits assets. The build receipt binds the exact archive bytes to the clean source commit; publication rejects changed bytes or a different checkout and pins the tag target. Re-verify remote main and the absence of an unexpected existing tag immediately before publication. The tag must identify the tested commit, not a later unrelated head. Do not rebuild between canary verification and publication.
7. Verify the published tarball/checksum and run the post-release canary check before announcing readiness. When implementation already authorizes canary testing, do not ask again merely because the check takes time. If an existing shared canary is used, hold its owning-host lock across the whole check and release it on every exit path.
8. Record tag, commit, artifact hash, tests, installation receipts and remaining exceptions in shared project evidence. A release is not proof that either operating host installed or certified it.

## Failure and rollback

- Stop release publication on red CI, unexpected files, a moved head, checksum mismatch or failed canary. Diagnose and prepare the specific repair; never conceal a failure behind a success message.
- Preserve failed canaries and snapshots for diagnosis. Do not delete a canary on failure. Create another isolated fixture or restore only known files after checking for later changes.
- A partial configuration apply retains its old VERSION and recovery evidence. Preserve concurrent edits; do not blanket-restore over them. Never restore an unsafe guard just because it was the previous version.
- Prefer a reviewed fix-forward or revert PR. Tag/release deletion, destructive reset, or force-push requires the operator's explicit destructive authorization and a snapshot. No fabricated confirmation markers.
- Draft downstream notices for review; do not send messages to other people without the operator's outbound authorization.

Source: accepted Workdesk reliability implementation plan and reconciled multi-session/release rules, September 8, 2026.
