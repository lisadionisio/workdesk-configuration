#!/usr/bin/env python3
"""Validate reference targets, not factual accuracy or heading existence.

Explicit paths must exist. Ambiguous basenames require a path unless a sibling
note resolves them. Folder navigation needs an explicit path; a bare folder
name is not proof of a note. Historical notes remain valid targets.
"""
import argparse
import collections
import os
from pathlib import Path
import re
import sys

EXCLUDE = {'.git', '.workdesk-backups', 'node_modules', 'vendor', '.obsidian', '.claude', '.codex', '.agents'}
SOURCE_EXCLUDE = {'_archive', 'defaults'}

def markdown_files(root, sources=False, attachments=False):
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE and d not in {'defaults'} and (not sources or d not in SOURCE_EXCLUDE)]
        rel = Path(base).relative_to(root).parts
        if sources and (rel[:2] in [('config', 'source'), ('system', 'session-log'), ('system', 'transcripts')]):
            dirs[:] = []; continue
        for name in names:
            if attachments or name.endswith('.md'):
                yield Path(base) / name

def references(text, wikilinks_only=False):
    fence = None
    for lineno, line in enumerate(text.splitlines(), 1):
        match = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if match:
            mark = match[1]
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence) and not line[match.end():].strip():
                fence = None
            continue
        if fence:
            continue
        # Match complete inline spans at their own delimiter width. Double/triple
        # backticks are documentation, not nested single-backtick references.
        def inline(m):
            ticks, body = m.group(1), m.group(2)
            if len(ticks) == 1 and re.match(r'^\[(ACTION|REVIEW|CONTENT|QUESTION|AWARENESS|WAITING)\]\s+', body):
                inline_refs.append(body)
            return ''
        inline_refs = []
        clean = re.sub(r'(`+)(.*?)(?<!`)\1(?!`)', inline, line)
        for target in inline_refs:
            if not wikilinks_only and '{' not in target and '...' not in target:
                yield lineno, target
        for target in re.findall(r'\[\[(.*?)\]\]', clean):
            target = target.replace(r'\|', '|').split('|')[0].split('#')[0].strip()
            if target and not any(x in target for x in ('{', '}', '...')):
                yield lineno, target

def resolve(target, source, root, index):
    target = target.removesuffix('.md')
    if '/' in target:
        if target.startswith(('./', '../')):
            candidates = [source.parent / target]
        else:
            candidates = [root / target, source.parent / target]
        for candidate in candidates:
            candidate = candidate.resolve()
            if not candidate.is_relative_to(root):
                continue
            if candidate.is_file() or candidate.with_name(candidate.name + '.md').is_file() or candidate.is_dir():
                return True
        return False  # a wrong explicit path never resolves by basename
    matches = index.get(target, [])
    if len(matches) == 1:
        return True
    if len(matches) > 1:
        # A same-directory note wins; otherwise require a disambiguating path.
        return (source.parent / (target + '.md') in matches or source.parent / target in matches)
    candidate = source.parent / target
    return candidate.is_file() or (root / target).is_file()

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-q', '--quiet', action='store_true')
    parser.add_argument('--require-links', action='store_true', help='Fail when any selected Markdown note has no outgoing wikilinks; use for generated knowledge notes, not raw sources.')
    parser.add_argument('targets', nargs='+')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    index = collections.defaultdict(list)
    for file in markdown_files(root, attachments=True):
        index[file.stem if file.suffix == '.md' else file.name].append(file)
    files = set()
    for name in args.targets:
        file = Path(name).resolve()
        if not file.exists() or not file.is_relative_to(root):
            print('Invalid or outside-vault input: ' + str(file), file=sys.stderr); return 2
        if file.is_dir():
            files.update(p for p in markdown_files(file, sources=True)
                         if p.relative_to(root).parts[:2] not in
                         [("config", "source"), ("system", "session-log"), ("system", "transcripts")])
        elif file.suffix == '.md':
            files.add(file)
    total = broken = missing = 0
    for file in sorted(files):
        text = file.read_text(encoding="utf-8")
        if args.require_links and not list(references(text, wikilinks_only=True)):
            missing += 1
            print(f'MISSING LINKS: {file.relative_to(root)} has no outgoing wikilinks.')
        for line, target in references(text):
            total += 1
            if not resolve(target, file, root, index):
                broken += 1
                print(f'BROKEN: {file.relative_to(root)}:{line} → {target}')
    if not args.quiet:
        print(f'Scanned {total} references; {broken} broken.' + (f' {missing} notes without outgoing wikilinks.' if args.require_links else ''))
    return int(broken > 0 or missing > 0)

if __name__ == '__main__':
    sys.exit(main())
