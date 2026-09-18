"""Find Gemini source IDs in bounded frontmatter, never in transcript prose.

Exit 0: one matching source; 1: none; 2: ambiguous/unreadable inventory.
No file is modified. Quoted keys or complex YAML identities are rejected for
review rather than guessed; emitted importer IDs use the supported scalar form.
"""
import json
import os
import re
import sys
from pathlib import Path

ID = re.compile(r'[A-Za-z0-9_-]+')
FIELD = 'gemini-doc-id'


def source_id(path):
    with path.open(encoding='utf-8-sig') as stream:
        if stream.readline().strip() != '---':
            return None
        found = None
        seen = False
        for index, line in enumerate(stream):
            if index >= 4096 or len(line) > 65536:
                raise ValueError('Unbounded source frontmatter')
            if line.strip() == '---':
                return found
            # Identify this key even if its YAML syntax needs manual review.
            key, separator, raw = line.partition(':')
            if not separator or key.strip().strip('\'"') != FIELD:
                continue
            if seen or key != FIELD:
                raise ValueError('Ambiguous source identity field')
            seen = True
            raw = raw.strip()
            if raw.startswith('"'):
                value = json.loads(raw)
            elif raw.startswith("'") and raw.endswith("'"):
                value = raw[1:-1].replace("''", "'")
            else:
                value = raw
            if not isinstance(value, str) or not ID.fullmatch(value):
                raise ValueError('Unsupported source identity scalar')
            found = value
        raise ValueError('Unterminated source frontmatter')


def find_sources(expected, roots):
    if not ID.fullmatch(expected):
        raise ValueError('Invalid document ID')
    matches = []
    for root in roots:
        if root.is_symlink() or (root.exists() and not root.is_dir()):
            raise ValueError('Source root is not an ordinary directory')
        if not root.exists():
            continue
        def fail(error):
            raise error
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail):
            for name in dirs + files:
                if (Path(directory)/name).is_symlink():
                    raise ValueError('Source inventory includes a symlink')
            for name in sorted(files):
                path = Path(directory)/name
                if path.suffix == '.md' and source_id(path) == expected:
                    matches.append(path)
    if len(matches) > 1:
        raise ValueError('Multiple notes claim the same source ID')
    return matches


def main():
    if len(sys.argv) != 4:
        raise ValueError('Expected document ID, intake and archive roots')
    matches = find_sources(sys.argv[1], [Path(p) for p in sys.argv[2:]])
    if matches:
        print(json.dumps(str(matches[0])))
        return 0
    return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, UnicodeError, ValueError):
        print('Source identity inventory is unreadable or ambiguous; reconcile before importing.', file=sys.stderr)
        sys.exit(2)
