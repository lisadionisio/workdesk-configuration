# Per-Project Knowledge Accounting

Every active project must have the 8-item minimum structure (9 for code projects). You must be able to look at any project and immediately see: what is the current state, what decisions have been made, what is outstanding, and is the knowledge current.

## When this applies

- Creating a new project folder in any projects location:
  - `atlas/projects/` (infrastructure and personal projects)
  - `atlas/businesses/{business}/projects/` (business-owned projects)
  - `atlas/clients/{client}/projects/` (client-owned projects)
- Updating any project-related information (meeting outcomes, decisions, deliverables)
- During context review at the start of any workflow
- When referencing a project during any processing or planning work

## What to do

- Every active project must have the 8-item minimum structure:
  1. `_brief.md` — Purpose, Principles, Outcome, Vision
  2. `_status.md` — Current phase, Next action, Open items, Last updated, Phases overview
  3. `plan.md` — The POBO output snapshot (or equivalent planning doc if the project wasn't POBO-planned)
  4. `notes/` — Running notes, meeting captures, brainstorm dumps
  5. `reference/` — Source material, research, inputs
  6. `specs/` — Detailed specs for builds and deliverables
  7. `deliverables/` — Final outputs
  8. `_archive/` — Retired material
- Code projects add a 9th item: `repo/` — either the local clone or a `README.md` stub pointing at the remote.
- When processing a meeting that produces project-relevant outcomes, update the project's `_status.md` in the same processing pass. Do not leave project statuses to go stale.
- When creating a new project, create the full 8-item (or 9-item) structure immediately. Use `/pobo` to scaffold it with populated files from a guided planning interview.
- When you encounter an active project that is missing a brief or status, flag it — but how you flag depends on context:
  - **During review, planning, or context work:** Create an inbox notification with `[ACTION]` prefix: "Project X is missing _brief.md" or "Project X status hasn't been updated since [date]."
  - **During build execution or Night Shift:** Note the gap in the session log for morning review. Do not create inbox notifications mid-execution — that's noise during focused work.
- During context review or planning work, check the `_status.md` date. If the last update is older than 14 days on an active project, flag it as stale.
- If the last-update date is missing, invalid, or cannot be interpreted from the source, report freshness as unknown. Flag the missing date for review; do not label the project stale, current, or eligible for closure on that basis.

## What NOT to do

- Do not reference a project as "active" when its `_status.md` hasn't been updated in over 14 days without flagging the staleness.
- Do not create project notes scattered across the vault without a project folder anchoring them. The project folder is the single source of truth.
- Do not assume a project is well-documented because it has many linked notes. Check for the brief and status specifically — those are the minimum.
- Do not create empty placeholder briefs or statuses. If you don't have enough information to write a meaningful brief, flag it as needing operator input rather than creating a hollow file.
