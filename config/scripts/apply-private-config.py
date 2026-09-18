#!/usr/bin/env python3
"""Reviewed private configuration overlays; companion to migrate.sh, not a new updater.

Dry run by default. Does not alter product defaults, VERSION, runtime state, hooks,
credentials or global paths. Reuses checked-file-copy.py for every replacement.
"""
import argparse, datetime, importlib.util, json, sys, tempfile
from pathlib import Path, PurePosixPath

spec = importlib.util.spec_from_file_location('checked', Path(__file__).with_name('checked-file-copy.py'))
checked = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checked)
PROTECTED = {'VERSION', 'settings.json', 'scripts/codex-pre-tool-use-guard.sh',
             'scripts/pre-tool-use-personal-lock.sh'}
ROOTS = {'scripts', 'skills', 'rules', 'templates', 'objects', 'sources', 'signals', 'practices', 'tools'}

def target_for(config, key):
    if not isinstance(key, str):
        raise ValueError('Target must be a config-relative path string')
    rel = PurePosixPath(key)
    if (not rel.parts or rel.as_posix() != key or rel.is_absolute() or '..' in rel.parts or '\\' in key or
        key in PROTECTED or any(p.startswith('.') for p in rel.parts) or
        (rel.parts[0] not in ROOTS and key != 'operator-policy.md') or
        'hooks' in rel.parts or 'guard' in rel.name or 'credential' in rel.name or
        rel.name in ('auth.json', 'client_secret.json')):
        raise ValueError('Private overlay target outside allowed config scope: ' + key)
    target = config / key
    if not target.resolve().is_relative_to(config.resolve()):
        raise ValueError('Target resolves outside configuration: ' + key)
    return target

def prepare(config, package):
    if (config / 'defaults').is_symlink():
        raise ValueError('Product defaults must not be a symlink')
    manifest = json.loads((package / 'manifest.json').read_text())
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list):
        raise ValueError('Private package requires a files list')
    if not isinstance(manifest.get('version'), str) or not manifest['version']:
        raise ValueError('Private package requires a version')
    planned, seen = [], set()
    for row in manifest['files']:
        key = row['target']
        target = target_for(config, key)
        identity = str(target.resolve()).casefold()
        if identity in seen: raise ValueError('Duplicate or case-equivalent target: ' + key)
        seen.add(identity)
        if target.exists() and not target.is_file():
            raise ValueError('Target is not a regular file: ' + key)
        source = (package / row['source']).resolve()
        if not source.is_relative_to(package.resolve()) or not source.is_file():
            raise ValueError('Source outside package or missing: ' + key)
        if source.stat().st_mode & 0o022:
            raise ValueError('Package source must not be group/world writable: ' + key)
        if checked.digest(source) != row['after_sha256']:
            raise ValueError('Package source hash mismatch: ' + key)
        in_product = (config / 'defaults' / key).is_file()
        expected_class = 'User overrides of product' if in_product else 'User config'
        if row['ownership'] != expected_class:
            raise ValueError('Ownership changed; review required: ' + key)
        actual = checked.digest(target)
        if actual == row['after_sha256']: state = 'no-op'
        elif actual == row['before_sha256']: state = 'ready'
        else: raise ValueError('Target changed since review: ' + key)
        planned.append((row, source, target, state))
    return manifest, planned

def run(vault, package, apply=False):
    config = vault / 'config'
    manifest, planned = prepare(config, package)
    report = {'package': manifest['version'], 'mode': 'apply' if apply else 'dry-run',
              'files': [{'target': row['target'], 'state': state} for row, _, _, state in planned]}
    if not apply or all(state == 'no-op' for _, _, _, state in planned):
        return report
    backup_base = vault / '.workdesk-backups'
    if backup_base.is_symlink():
        raise ValueError('Backup directory must not be a symlink')
    backup_base.mkdir(exist_ok=True)
    recovery = Path(tempfile.mkdtemp(prefix='private-overlay-', dir=backup_base))
    receipt = {'package': manifest['version'], 'status': 'partial',
               'as_of': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'files': []}
    try:
        for row, source, target, state in planned:
            snapshot = recovery / 'before' / row['target']
            result = checked.checked_copy(source, target, row['before_sha256'], snapshot)
            receipt['files'].append({'target': row['target'], 'result': result,
                'before_sha256': row['before_sha256'], 'after_sha256': checked.digest(target),
                'snapshot': str(snapshot.relative_to(recovery)) if snapshot.exists() else None})
        receipt['status'] = 'applied'
    finally:
        (recovery / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        if receipt['status'] == 'partial':
            print('Partial installation recovery receipt: ' + str(recovery / 'receipt.json'), file=sys.stderr)
    report['receipt'] = str(recovery / 'receipt.json')
    return report

def restore_file(vault, receipt_path, key):
    root = receipt_path.resolve().parent
    if not root.is_relative_to((vault / '.workdesk-backups').resolve()):
        raise ValueError('Receipt must be in this vault backup directory')
    target = target_for(vault / 'config', key)
    rows = json.loads(receipt_path.read_text())['files']
    row = next((r for r in rows if r['target'] == key), None)
    if row is None or row['snapshot'] is None:
        raise ValueError('No previous file snapshot; retain the added file for reviewed disposition')
    source = (root / row['snapshot']).resolve()
    if not source.is_relative_to(root) or checked.digest(source) != row['before_sha256']:
        raise ValueError('Recovery snapshot missing or changed')
    recovery = Path(tempfile.mkdtemp(prefix='per-file-restore-', dir=vault / '.workdesk-backups'))
    result = checked.checked_copy(source, target, row['after_sha256'], recovery / 'before-file')
    receipt = {'target': key, 'result': result, 'sha256': checked.digest(target), 'source_receipt': str(receipt_path)}
    (recovery / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, required=True)
    parser.add_argument('package', type=Path, help='Package directory, or receipt.json with --restore-file')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--restore-file', metavar='CONFIG_RELATIVE_PATH')
    args = parser.parse_args()
    if args.restore_file:
        if not args.apply: parser.error('Per-file restoration requires --apply')
        result = restore_file(args.vault, args.package, args.restore_file)
    else: result = run(args.vault, args.package, args.apply)
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    try: main()
    except (ValueError, KeyError, OSError, checked.ChangedTarget) as exc:
        raise SystemExit(str(exc))
