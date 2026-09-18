# Session Entry Behavior

When the SessionStart hook injects session-entry context (unprocessed transcripts, intake items, due signals, version updates), treat it as **background state, not the operator's agenda**. The hook exists so the operator has visibility into pending work — not so Claude can push a checklist at them.

## When this applies

- The first turn of any Claude Code session where a SessionStart hook has injected a host-local session-entry report path
- Any turn where session-entry context appears in a system reminder

## What to do

- **If the operator's message contains a clear task or ask:** Act on it immediately. Do not lead with session-entry items, do not offer them as a menu, do not ask which to tackle first.
- **If there is non-trivial pending state, open with a single one-line blurb before doing the work.** Example: "Heads up: 3 unsummarized session logs, daily-plan + weekly-review due, v1.2.9 available — say the word if you want to address any of those." Then proceed with the operator's actual ask.
- **If the session-entry state is trivial (0 transcripts, 0 intake, no due signals, no update available):** Skip the blurb entirely. Just do the work.
- **If the operator gave no ask (cold start with no prompt):** Then it is appropriate to surface the session-entry items as a menu and ask what they want to start with.

## What counts as "non-trivial"

Surface a blurb when any of these are true:
- ≥1 unprocessed transcript
- ≥1 intake item
- ≥1 due signal (daily-plan, weekly-review, etc.)
- A WorkDesk OS update is available

If none apply, no blurb.

## What NOT to do

- Do not lead a session by listing session-entry items when the operator gave a clear task. The operator's ask takes precedence over the hook's reminders.
- Do not phrase the blurb as a question that requires an answer before proceeding ("Which would you like to start with?"). The blurb is a reminder; the work is the ask.
- Do not repeat the blurb on subsequent turns within the same session. Once is enough.
- Do not silently drop session-entry items the operator should know about. The blurb exists so they have the chance to redirect.

The host-local report is derived state, not a shared outcome. Read the path emitted by the current SessionStart hook. The former `config/state/session-entry.md` is historical and must not be used as a current scan.
