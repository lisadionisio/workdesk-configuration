#!/usr/bin/env python3
"""Calculate due signals without weekday gates. Missing/malformed dates stay visible."""
import datetime as dt
import json
import sys
from pathlib import Path

def due_signals(state, today):
    result = []
    for name, days in (("daily-plan", 1), ("weekly-review", 7), ("vault-improvements", 7)):
        row = state.get(name) or {}
        if not isinstance(row, dict):
            row = {}
        try:
            suppressed = dt.date.fromisoformat(str(row.get("suppressed-until")).split("T", 1)[0])
        except ValueError:
            suppressed = None
        if suppressed and today < suppressed:
            continue
        try:
            last = dt.date.fromisoformat(str(row.get("last-fired")).split("T", 1)[0])
        except ValueError:
            last = None
        if last is None or last > today or (today - last).days >= days:
            result.append(name)
    return result

if __name__ == "__main__":
    path = Path(sys.argv[1])
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("signal state must be an object")
    except (ValueError, OSError) as exc:
        print("Signal state unreadable: " + str(exc), file=sys.stderr)
        sys.exit(2)
    print("\n".join(due_signals(state, dt.date.today())))
