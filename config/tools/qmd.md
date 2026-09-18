---
tool: qmd
slug: qmd
category: cli
class: first-class
connected: false
added-on: 2026-04-29
connector: cli
preferred-for: []
confirmed-by-operator: false
---

## What it is

Hybrid semantic + keyword search over the vault's markdown files. Surfaces conceptually related notes, not just literal matches. Multiple WorkDesk skills (briefings, research, intel, transcripts) call `qmd query` to find related context.

## Best practices

- `qmd query "topic"` — short, focused topic phrases work best for semantic search.
- `qmd status` — check index health when results look stale.
- Use `grep` for literal string searches; use `qmd` for "what notes are about this idea?"
- Search freshness depends on an explicitly configured refresh process — see Connection notes.

## Connection notes

**Install:** Use the official npm package `@tobilu/qmd` with a reviewed version compatible with the host's Node runtime. Record the actual executable path and version per host; do not assume a Homebrew installation.

**Verification:** Resolve `command -v qmd` in each supported runtime, confirm the intended vault collection path and scope, check indexed counts and embedding coverage, then retrieve a known note and compare it with its source. Package presence or a zero exit status alone is insufficient. Keep `connected: false` until the intended search use is verified.

**Vectorization runner — open question.** qmd needs a periodic process to re-vectorize the vault as files change. Where the runner lives (`config/scripts/`? a launchd plist? cron?), how often it runs, and whether `/workdesk-doctor` checks freshness — TBD for V1.1. Tracked in `atlas/projects/workdesk/specs/onboarding-redesign.md` Open Items §7.

## Linked use cases

- *(filled in as skills declare they use qmd)*
