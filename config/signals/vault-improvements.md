---
type: signal-declaration
name: vault-improvements
shape: vault-improvement
output-folder: intel/vault-improvements/
naming: "{YYYY-MM-DD}-vault-improvements"
schedule: weekly
suppressed-for-first-days: 14
version: 1.0
---

# Signal: vault-improvements

The self-improvement loop. Weekly holistic scan across the whole vault.

**Suppressed for first 14 days** so weekly-review can stabilize first. After day 15, fires weekly.

## Purpose

Spot patterns and gaps that no individual skill would catch. Propose declaration changes, rule promotions, and cleanup via `[REVIEW]` inbox.

## Sources

- `system/events/{YYYY-MM}.md` (current month + prior month — 7-30 day coverage)
- Unused atlas folders (no writes in 30+ days)
- Stale projects, stale areas (`1.5 × expected-cadence` exceeded)
- `gtd/inbox/` backlog (size + age)
- Signals not opened (output files exist but operator never read them — check `last-touched`)
- Signals firing on empty (output text < 200 chars repeatedly)
- Skill `learnings.md` files
- Declaration `## Learnings` sections
- Captures sitting unprocessed in `system/intake/`
- **Broken wikilinks** — `[[...]]` references whose target file doesn't exist
- **Missing required frontmatter** — atlas notes without `source:`, signals without `sources:`
- **Oversized media** in `system/media/` (>50MB single file)
- **Recurring items overdue** (`status: active`, `next_due` past today by > one cadence interval)

## Output

`intel/vault-improvements/{YYYY-MM-DD}-vault-improvements.md` + `[REVIEW]` inbox pointers for each recommendation. Body sections:

1. **Cross-skill correction patterns** — corrections appearing in ≥2 skills' `learnings.md` within 30 days; propose promotion (per `claude-md-coevolution` rule)
2. **Stale contexts** — projects/areas exceeding their cadence
3. **Broken-link scan** — wikilinks pointing nowhere; propose rewrites or deletions
4. **Frontmatter health** — missing required fields
5. **Inbox backlog** — size, age, prefix mix; suggest triage if >20
6. **Empty signals** — signals producing thin output; propose tuning the declaration
7. **Oversized media** — files >50MB; suggest external storage or compression

## Schedule mechanism

`SessionStart` reads `config/state/signals.json`. If `suppressed-until` is set and today is before that date, signal stays silent. Bootstrap sets `suppressed-until` to install date + 14 days. After day 15, fires weekly using the same `last-fired` mechanism as weekly-review.

## Detection (ad-hoc)

Surface ad-hoc generation when:
- Operator asks "what's drifting" or "audit the vault"
- A specific failure (broken-link error in another skill) suggests a vault-wide scan would help

## ## Learnings

(Empty.)

Missing or corrupt signal state is an explicit readiness failure; do not invent an installation date or silently drop the initial suppression period. Repair state from verified installation evidence. Dates use `YYYY-MM-DD`; legacy ISO datetimes are normalized to their date for cadence checks.
