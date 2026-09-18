---
type: signal-declaration
name: daily-plan
shape: briefing
output-folder: intel/briefings/daily/
naming: "{YYYY-MM-DD}-daily-plan"
schedule: daily
version: 1.2
---

# Signal: daily-plan

Generate today's daily plan. Be contextual and timely, and end with judgment — not a data dump. Pull whatever's relevant for what's happening today, regardless of how far back the source is, then reason about it (see [Reasoning — focus & GTD triage](#reasoning--focus--gtd-triage)) to keep the operator focused on the work that truly requires them.

Graph-traverse from today's anchors. Two operator-configurable windows apply (see [Configuration](#configuration)): **daily notes — last `{lookback}` days** (today weighted most), and **calendar — today + next `{lookahead}` days** (today is the focus; the lookahead exists to catch anything that needs an action *today* to be ready). All other sources have no hardcoded window.

## Purpose

Operator opens Claude Code in the morning (or whenever they start) and the daily plan is either freshly generated or the cue to generate it.

## Configuration

This signal reads operator-owned settings from `config/operator-profile.md` (frontmatter `daily-plan:` block + `email`). All have defaults — a missing field falls back to its default, so the signal degrades gracefully for any operator. Resolve these first:

| Setting | Source | Default | Used for |
|---|---|---|---|
| `{email}` | `operator-profile.email` | — (if empty, calendar scoping falls back to `primary`) | Calendar id when scope is `own` |
| `{scope}` | `operator-profile.daily-plan.calendar-scope` | `own` | `own` = only the operator's calendar; `all` = every shared calendar |
| `{lookahead}` | `operator-profile.daily-plan.calendar-lookahead-days` | `7` | Days ahead to scan the calendar |
| `{lookback}` | `operator-profile.daily-plan.daily-note-lookback-days` | `7` | Days of daily notes to read |
| `{label}` | `operator-profile.daily-plan.action-email-label` | `""` (skip email pull) | Gmail label pulled as action candidates |
| `{exclude}` | `operator-profile.daily-plan.exclude-calendars` | `[]` | Calendars to always exclude |

Substitute these values everywhere they appear below as `{email}`, `{scope}`, `{lookahead}`, `{lookback}`, `{label}`.

## Anchors

Zones (read from vault):
- **Calendar — today + next `{lookahead}` days, scoped per `{scope}`.** When `{scope}` is `own` (default), restrict to the operator's own calendar (`{email}`, or `primary` if `{email}` is empty) and exclude shared/subscribed calendars (plus anything in `{exclude}`). When `{scope}` is `all`, include shared calendars but still drop `{exclude}`. Today is the focus; the lookahead exists to catch anything that needs an action *today* to be ready (prep, materials, a reply, scheduling). See Tools below for exact invocation.
- **Daily notes — last `{lookback}` days.** Read every `personal/daily/YYYY.MM.DD Daily Note.md` falling within the last `{lookback}` calendar days (skip days with no note — there are gaps). Weight **today's** note most heavily; older notes supply carry-over context, open loops, and unfinished intentions. Read-only — never modify daily notes.
- `gtd/inbox/` items (with backlog warning if >20 unresolved)
- `system/intake/` items broken down by `source-kind` (transcript / bookmark / other) — see [Intake + pull-health surfacing](#intake--pull-health-surfacing) below
- **Active projects across all three locations:**
  - `gtd/projects/*/_status.md` — infrastructure & personal projects
  - `atlas/clients/*/projects/*/_status.md` — client-owned projects
  - `atlas/businesses/*/projects/*/_status.md` — business-owned projects
  Include where status is `active` (or `_status.md` shows a current/active phase).
- **Actions:** `gtd/actions/next/` (all open next-actions) and `gtd/actions/waiting/` (delegated / waiting-on items — check whether any are now unblocked or need a nudge).
- due `gtd/recurring/schedules/` items (`status: active` AND `next_due <= today`)

Tools (try if connected per `config/tools/<slug>.md` `connected: true`; record unavailable, not-configured, stale and verified-empty sources separately):
- **`gws calendar` — scoped per `{scope}`.**
  - When `{scope}` is `own` (default): scope every call to `{email}` (or `primary` if `{email}` is empty), so shared/subscribed calendars are excluded.
    - Today: `gws calendar +agenda --today --calendar {email}`
    - Next `{lookahead}` days: `gws calendar +agenda --days {lookahead} --calendar {email}`
    - Explicit equivalent: `gws calendar events list --params '{"calendarId":"{email}","timeMin":"<today 00:00 ISO>","timeMax":"<today+{lookahead} 23:59 ISO>","singleEvents":true,"orderBy":"startTime"}'` (use `"primary"` for the calendarId if `{email}` is empty).
    - **Do NOT** use bare `+agenda` / `+agenda --today` without `--calendar` under `own` scope — it returns events across every shared calendar.
  - When `{scope}` is `all`: use `+agenda --days {lookahead}` (no `--calendar`), then drop any event whose calendar is in `{exclude}`.
  - Verify the returned events' `"calendar"` field matches the intended scope before trusting it.
- **`gws gmail` — the `{label}` label.** Only if `{label}` is non-empty (default empty → skip this pull entirely). Pull threads under the operator's `{label}` Gmail label as action candidates:
  - `gws gmail users messages list --params '{"userId":"me","q":"label:{label}","maxResults":25}'` (quote the label in the query if it contains spaces: `label:\"{label}\"`).
  - For each id, fetch metadata: `gws gmail users messages get --params '{"userId":"me","id":"<id>","format":"metadata"}'`, then read `payload.headers` for Subject / From / Date. (NOTE: `userId:"me"` is required; do NOT pass a `metadataHeaders` array — it breaks the gws request. Parse all headers and filter in code.)
  - Such a label is typically a large standing archive (hundreds of threads), not a clean current queue — triage for what's genuinely live now (do-now / prioritize / delegate / defer / delete); don't dump the whole label.
  - `gws gmail +triage` (unread) is a useful secondary signal; the `{label}` label is the priority pull when set.

## Traversal

1. For each person on today's calendar: fetch their note + last meeting (no time cap)
2. For each project referenced today (across `gtd/projects/`, `atlas/clients/*/projects/`, `atlas/businesses/*/projects/`): fetch `_status` + recent meetings tied to it
3. Surface stale work: projects whose `last-touched` exceeds `1.5 × expected-cadence` (where `expected-cadence: none` is excluded)
4. For each `gtd/actions/waiting/` item: check whether the blocker has cleared or a follow-up is now due — these become "delegate / nudge" candidates in triage

## Reasoning — focus & GTD triage

Preserve the status of source statements. An intention in a daily note is a candidate, not a confirmed commitment; an incomplete checklist is not proof that an external action remains undone. Cite the source and keep unresolved contradictions visible. Do not invent a due date, owner, completion, client agreement or attendee from context. Proposed prioritization and delegation remain recommendations until explicitly accepted or supported by a current source.

The daily-plan is not a data dump. After gathering all anchors, apply judgment — this is the highest-value part of the signal. The operator is being briefed AND coached to stay focused and sharp.

For every candidate item (calendar prep, project next-step, next-action, `action items` email, waiting-on, inbox), assign a GTD disposition:

- **Do now / today** — high-impact AND genuinely requires the operator. This is the short list that leads the plan. Be ruthless: most things are not this.
- **Prioritize** — important and requires the operator, but not strictly today. Sequence it; name when.
- **Delegate** — someone else can or already does own it. Name who and what the handoff is. Includes "waiting on X — nudge them."
- **Defer** — real but not now. Park it with a trigger or date so it stops occupying attention.
- **Delete** — no longer matters, duplicate, or stale. Say so plainly so the operator can clear it.

Rules of thumb:
- The **Focus** section names **1–3 items**, not ten. If everything is a priority, nothing is.
- Prefer work only the operator can do (decisions, relationships, judgment, creation) over work that is merely on their list.
- A calendar-lookahead item earns a "do-now" slot only when something in the next `{lookahead}` days needs an action *today* to be ready (e.g., a Thursday pitch needs the deck drafted today).
- Surfacing "you can drop this" or "this isn't yours" is as valuable as surfacing "do this."
- Tone: a sharp chief-of-staff briefing — direct, warm, no filler (per [[writing-style]]). Brief over exhaustive.

## Sparse-data fallback chain

Try in order. Skip verified-empty layers when selecting work, but retain material coverage gaps in the output. An unread, unavailable, not-configured or stale source is not a verified-empty layer.

1. Calendar commitments (today + next `{lookahead}` days, scoped per `{scope}`)
2. `gtd/actions/next/` (sorted by `parent:` recency)
3. Due recurring items from `gtd/recurring/schedules/`
4. Active project `_status.md` summaries
5. Today's daily note
6. Unread inbox items (with backlog warning if >20)
7. Stale contexts needing attention

If all 7 layers are verified empty (true cold-start), produce a **setup-oriented plan**: "Nothing scheduled and nothing in next-actions. First steps to seed the vault: …" — never a hollow report. If some layers were not checked or could not be read, state the limited coverage and use the available sources; do not declare a cold-start from failed reads.

## Output

`intel/briefings/daily/{YYYY-MM-DD}-daily-plan.md`:

```yaml
---
type: signal
shape: briefing
date: 2026-04-26
sources: ["[[...]]", "[[...]]"]
schedule: daily
---
```

Before the body, include **Coverage** — identify checked sources and their dates/account scopes; state material missing, stale or unavailable inputs. Mark the plan partial when required coverage is incomplete.

Body sections:
1. **Focus — the 1–3 things that truly require *you* today.** Lead with this. Synthesized via GTD triage (see [Reasoning](#reasoning--focus--gtd-triage)) from everything below. High-impact work only the operator can do.
2. Today's commitments + relevant context for each (scoped-calendar events; today first, then any lookahead items that need action today)
3. Projects to advance + where you left off (across `gtd/` / `atlas/clients/` / `atlas/businesses/`)
4. Open actions & email — `gtd/actions/next/`, `{label}`-labeled email (if set), and `gtd/actions/waiting/` items, each tagged with a GTD disposition (do-now / prioritize / delegate / defer / delete)
5. Stalled items needing attention
6. Inbox items awaiting triage

Tonality respects `config/operator-profile.md` `role`, `work-mode`, and `first-30-days-mode`. During `first-30-days-mode: active`, lean toward setup-oriented guidance ("you have 2 active projects; weekly-review will surface stale ones"). If `role` or `work-mode` is empty (early state), default to neutral.

## Intake + pull-health surfacing

The daily intake folder (`system/intake/`) holds raw sources awaiting
`/process-transcripts` or similar processing skills. The daily-plan surfaces
intake state in two views:

### 1. Count breakdown by source-kind

Walk `system/intake/*.md`, read each frontmatter `source-kind:` (and
`source-format:` where present) and emit:

```
Intake awaiting processing:
  - Transcripts (Granola):   N items   (~M minutes to process)
  - Transcripts (Google):    N items
  - Bookmarks:               N items
  - Other / unlabeled:       N items
```

Processing-time estimate is rough — 5 min per bookmark, 30 min per
≤500-utterance transcript, 60 min per >500-utterance transcript. The
estimate sets operator expectations, not a hard contract.

If total intake > 20, surface a triage warning as the day's top item.

### 2. Pull-health check

Resolve the current host-local state paths from each installed puller's tool guide and runtime configuration. For account-scoped sources, report each configured account separately. Historical `config/state/pull-{source}.json` files below are legacy evidence, not automatically the active checkpoint:

- `config/state/pull-granola.json`
- `config/state/pull-google.json`
- (future) any new `pull-*.json` for additional sources

For each, compute `now - last_success_at`. If > 36 hours:

```
⚠ Pull health: <source> last successful pull was X hours ago (state: <state-file-path>)
  Investigate: run `bash config/scripts/pull-<source>.sh --status` to see failure history;
  re-auth (Infisical or gws) if needed; rerun manually to catch up.
```

If `consecutive_failures > 0` in any state file, surface that alongside the
hours-since count regardless of staleness.

This makes silent cron failures visible — the operator sees the gap the next
morning instead of discovering it weeks later when transcripts are missing.

## Schedule mechanism

`SessionStart` reads `config/state/signals.json`. If `daily-plan.last-fired` is before local midnight (today 00:00), session-entry adds a notice proposing `/daily-ops`. After a successful write, the skill updates `daily-plan.last-fired` to today.

## Detection (proactive proposal beyond the daily schedule)

Surface ad-hoc generation when:
- Operator asks "what's on my plate" or equivalent
- Mid-day context shift (long break) and existing daily-plan is stale relative to new calendar events

## ## Learnings

- **2026-06-09/10 — v1.1→1.2 parameterization.** Operator expanded Phase 2 (own-calendar-only scoping, today+7d lookahead, 7d daily-note lookback, an action-email-label pull, multi-location project scan across `gtd/`+`atlas/clients/`+`atlas/businesses/`, `gtd/actions/waiting/` check, and GTD do/prioritize/delegate/defer/delete triage with a leading Focus section). Initially hardcoded the operator's values into the signal; refactored so the generalized logic ships upstream and the operator-specific values (`email`, scope, windows, label) live in `operator-profile.daily-plan` with defaults. This is the same pattern as `week-start`/`role`/`work-mode` — signals read operator-owned prefs from the profile, which `/update` preserves.
- **gws gmail metadata quirk.** When fetching message metadata, pass `userId:"me"` and `format:"metadata"` only — do NOT pass a `metadataHeaders` array; it breaks the gws request and returns empty headers. Parse all `payload.headers` and filter Subject/From/Date in code.
- **Label-name reality.** The operator's "action items" label is literally named `Actions` (a parallel `Waiting` label also exists). Don't assume a label's display name from how the operator refers to it — list labels (`gws gmail users labels list`) to confirm the exact name. Now read from `operator-profile.daily-plan.action-email-label`.

## Coverage and unresolved questions

For each source, retain what was read, the time window, the result and any observed error. Distinguish available, verified-empty, not-configured, unavailable, not-checked and stale. Include a compact Coverage summary with material gaps and recovery steps where known. A missing supplied input or an out-of-scope read is not a failed provider request. Never attribute a local action, waiting, project or inbox omission to a calendar/CRM/search outage unless an actual dependency and failure are established. Continue authorized local reads independently. Do not infer no commitments from a failed read or invent its cause.

An unresolved QUESTION remains active regardless of age. Archive only after a sourced resolution or explicit operator dismissal; preserve the resolution and its link. Age never resolves a truth conflict.
