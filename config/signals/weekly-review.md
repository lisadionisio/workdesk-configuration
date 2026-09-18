---
type: signal-declaration
name: weekly-review
shape: briefing
output-folder: intel/briefings/weekly/
naming: "{YYYY-MM-DD}-weekly-review"
schedule: weekly
mandatory: true
version: 1.0
---

# Signal: weekly-review

The bridge between onboarding and stable use. **Active from week 1.** The most important signal in the first 30 days.

## Purpose

End-of-week (or start-of-week, depending on `operator-profile.week-start`) holistic scan that surfaces:
- proposed action closures (completed but not archived)
- proposed promotions (action → project, recurring → action)
- stale contexts needing operator attention
- inbox backlog
- recurring items due in the next 7 days

## Anchors

- `gtd/actions/next/` and `gtd/actions/waiting/` (status, last-touched)
- Project status and brief across `gtd/projects/`, `atlas/businesses/*/projects/`, and `atlas/clients/*/projects/`
- `gtd/recurring/schedules/` (`status: active` AND `next_due <= today + 7d`)
- `gtd/inbox/` (full backlog)
- Stale contexts: `1.5 × expected-cadence` exceeded
- Last week's daily notes
- Processed transcripts since last weekly-review

## Output

`intel/briefings/weekly/{YYYY-MM-DD}-weekly-review.md` + `[REVIEW]` inbox pointers for proposed closures, promotions, and cleanup. Body sections:

1. **What you shipped** — completed actions, archived projects, decisions made
2. **What's stalled** — stale contexts, blocked actions, inbox backlog over 20
3. **What's coming** — recurring items due in 7 days, deadlines on the calendar
4. **Proposed closures + promotions** — listed; each has a `[REVIEW]` pointer in inbox

## Schedule mechanism

`SessionStart` reads `config/state/signals.json`. If `weekly-review.last-fired` is missing, invalid, in the future, or at least seven days old (unless explicitly suppressed), session-entry adds a notice proposing `/weekly-review`. After successful write, skill updates `weekly-review.last-fired` to today.

## Detection (ad-hoc)

Surface generation when:
- Operator says "review the week" or equivalent
- The previous weekly-review is older than 8 days (overdue)

## ## Learnings

(Empty.)
