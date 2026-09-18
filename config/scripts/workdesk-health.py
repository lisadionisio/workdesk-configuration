#!/usr/bin/env python3
"""Read-only local capability, backup and search metadata report.

Does not contact providers. Exit zero means a report was produced, not healthy.
JSON status is attention for observed exceptions and incomplete for unverified
search coverage without observed exceptions. Read both exceptions and unverified;
metadata alone never certifies source freshness, all chunks or retrieval quality.
"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time


def backup_status(pending, commit_age, push_pending_age, marker_known, head_pushed):
    issues = []
    if commit_age is None:
        issues.append("commit-history-unavailable")
    elif pending and commit_age > 1800:
        issues.append("pending-changes-no-commit-30m")
    if not marker_known:
        issues.append("push-marker-unavailable")
    elif not head_pushed and push_pending_age is None:
        issues.append("push-ancestry-unverified")
    elif not head_pushed and push_pending_age > 7200:
        issues.append("unpushed-commit-older-than-2h")
    return issues


def plugin_status(plugin, data):
    try:
        plugins = json.loads(plugin.read_text())
        settings = json.loads(data.read_text())
        if not isinstance(plugins, list) or any(not isinstance(x, str) for x in plugins) or not isinstance(settings, dict):
            raise ValueError('Invalid backup settings structure')
        cadence = settings.get('autoSaveInterval')
        pull_interval = settings.get('autoPullInterval', 0)
        pull_boot = settings.get('autoPullOnBoot', False)
        for value in (cadence, pull_interval):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError('Invalid backup interval')
        if not isinstance(pull_boot, bool):
            raise ValueError('Invalid pull setting')
        enabled = 'obsidian-git' in plugins
        automatic = enabled and cadence > 0
        auto_pull = pull_boot or pull_interval > 0
        issues = []
        if not enabled: issues.append('automatic-commit-plugin-disabled')
        elif not automatic: issues.append('automatic-commit-interval-disabled')
        if auto_pull: issues.append('automatic-pull-enabled')
        return enabled, automatic, auto_pull, cadence, issues
    except (OSError, ValueError, TypeError):
        return None, None, None, None, ['backup-plugin-state-unavailable']


def qmd_index_status(index, vault):
    """Observe QMD 2.x metadata only; never initialize or modify its database."""
    result = {'index': str(index), 'state': 'unavailable',
              'freshness': 'unverified', 'semantic_completeness': 'unverified',
              'limits': ['Metadata does not prove current source coverage, all chunk embeddings, or retrieval quality.']}
    if not index.is_file():
        return dict(result, reason='index-file-missing')
    try:
        connection = sqlite3.connect(index.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            connection.execute('PRAGMA query_only = ON')
            collections = connection.execute('SELECT name, path, pattern FROM store_collections').fetchall()
            matches = [(name, pattern) for name, path, pattern in collections
                       if Path(path).resolve() == vault.resolve()]
            if not matches:
                return dict(result, reason='vault-collection-missing')
            observations = []
            for name, pattern in matches:
                count, hashes = connection.execute(
                    'SELECT COUNT(*), COUNT(DISTINCT hash) FROM documents WHERE active=1 AND collection=?',
                    (name,)).fetchone()
                pending = connection.execute(
                    'SELECT COUNT(DISTINCT d.hash) FROM documents d WHERE d.active=1 AND d.collection=? '
                    'AND NOT EXISTS (SELECT 1 FROM content_vectors v WHERE v.hash=d.hash AND v.seq=0)',
                    (name,)).fetchone()[0]
                observations.append({'name': name, 'pattern': pattern, 'active_documents': count,
                                     'unique_hashes': hashes, 'hashes_without_first_vector': pending})
            return dict(result, state='index-observed', collections=observations)
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return dict(result, reason='index-unreadable-or-unsupported-schema')


def search_summary(search):
    """Separate observed defects from limits of this read-only metadata probe."""
    issues, unchecked = [], []
    if search.get('state') == 'not-checked':
        unchecked.append('search-not-checked')
    elif search.get('state') != 'index-observed':
        issues.append('search-' + search.get('reason', 'unavailable'))
    else:
        for collection in search['collections']:
            if collection['active_documents'] == 0:
                issues.append('search-empty-collection:' + collection['name'])
            if collection['hashes_without_first_vector'] > 0:
                issues.append('search-missing-first-vectors:' + collection['name'])
        unchecked.extend(['search-source-freshness-unverified',
                          'search-all-chunk-coverage-unverified',
                          'search-retrieval-quality-unverified'])
    return issues, unchecked


def inspect(vault, qmd_index=None):
    def git(*args):
        r = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    now = time.time()
    head = git("rev-parse", "HEAD")
    stamp = git("log", "-1", "--format=%ct")
    commit_age = max(0, int(now) - int(stamp)) if stamp else None
    pending = git("status", "--porcelain")
    marker_path = git("rev-parse", "--git-path", "last-backup-push")
    marker = Path(marker_path) if marker_path else None
    if marker and not marker.is_absolute(): marker = vault / marker
    pushed = marker.read_text().strip() if marker and marker.is_file() else None
    age = None
    if pushed and head != pushed:
        ancestry = subprocess.run(["git", "-C", str(vault), "merge-base", "--is-ancestor", pushed, "HEAD"], capture_output=True)
        if ancestry.returncode == 0:
            stamps = git("log", "--reverse", "--format=%ct", pushed + "..HEAD")
            age = max(0, int(now) - int(stamps.splitlines()[0])) if stamps else None
    issues = backup_status(bool(pending), commit_age, age, bool(pushed), head == pushed)
    plugin = vault / '.obsidian/community-plugins.json'
    data = vault / '.obsidian/plugins/obsidian-git/data.json'
    enabled, automatic, auto_pull, cadence, plugin_issues = plugin_status(plugin, data)
    issues.extend(plugin_issues)
    if pending is None: issues.append('working-tree-unavailable')
    if not (Path.home()/'.claude/hooks/verify-send-phrase.py').is_file(): issues.append('email-verifier-missing')
    if not (Path.home()/'.claude/email-send-phrase').is_file(): issues.append('email-phrase-not-provisioned')
    search = qmd_index_status(qmd_index, vault) if qmd_index else {'state': 'not-checked', 'reason': 'no-explicit-qmd-index'}
    search_issues, unchecked = search_summary(search)
    issues.extend(search_issues)
    return {
        'as_of': dt.datetime.now(dt.timezone.utc).isoformat(),
        'host': __import__('socket').gethostname(),
        'vault': str(vault),
        'status': 'attention' if issues else ('incomplete' if unchecked else 'no-local-exception-detected'),
        'exceptions': issues,
        'unverified': unchecked,
        'backup': {'commit_age_seconds': commit_age, 'pending_change_count': len(pending.splitlines()) if pending is not None else None,
                   'head_matches_local_push_receipt': bool(head and pushed and head == pushed), 'oldest_unpushed_age_seconds': age,
                   'automatic_commit_plugin_enabled': enabled, 'automatic_commits_enabled': automatic, 'commit_interval_minutes': cadence, 'automatic_pull_enabled': auto_pull},
        'tools_on_this_path': {name: bool(shutil.which(name)) for name in ['claude','codex','gws','qbo','qmd','ntn','keep-markdown','infisical']},
        'search': search,
        'safety': {'codex_wiring_present': (vault/'.codex/hooks.json').is_file(),
                   'verifier_present': (Path.home()/'.claude/hooks/verify-send-phrase.py').is_file(),
                   'phrase_present': (Path.home()/'.claude/email-send-phrase').is_file(),
                   'runtime_certification': 'see implementation receipts; file presence is not protection'},
        'limits': ['No remote freshness or visibility check performed.', 'Sleep/offline time is not subtracted; inspect host availability before escalating an age warning.',
                   'Tool presence does not establish account readiness.', 'No secrets or provider payloads were read.']}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--qmd-index', type=Path, help='Optional explicit QMD SQLite index; metadata is inspected read-only')
    args = parser.parse_args()
    print(json.dumps(inspect(args.vault.resolve(), args.qmd_index), indent=2))
