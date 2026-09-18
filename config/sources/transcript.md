---
type: source-declaration
name: transcript
zone: system
location: system/transcripts/
naming: "{YYYY-MM-DD-or-undated}-{topic-slug}"
move-after-processing: false
version: 1.1
---

# Source: transcript

Granola, Google Meet, or manual meeting transcripts. Raw input awaiting processing.

## Format

```yaml
---
type: source
source-kind: transcript
date: 2026-04-26
processed: false
processed-into: []
---
```

Body: verbatim transcript text.

`date` is the meeting occurrence date, not the import date. If that date is
unknown, preserve the raw source with `date: null` and an `undated-` filename;
`pulled-at` may separately record retrieval time. A document-only Gemini pull
has no calendar context, so it leaves `event-start` and `event-organizer` null.
Resolve the occurrence date from source evidence or explicit operator input
before creating a complete dated meeting record. Do not infer it from the
filename of an undated source, retrieval time or document modification time.

## Processing rule

Session-entry intake scan or `/process-transcripts` proposes extraction. Operator confirms. Then:
1. Create `atlas/meetings/{date}-{topic}.md`
2. Update each attendee's person note (matching rule)
3. Create `atlas/decisions/` for durable decisions
4. Create `[REVIEW]` proposals for action creations (subject to flood guard)
5. Flip transcript frontmatter `processed: true` and update `processed-into:` with backlinks

Dropping a file does NOT auto-process. Always operator-confirmed.

## Retention

Default `move-after-processing: false`. Transcripts persist in `system/transcripts/` after processing — never deleted, link integrity preserved.
