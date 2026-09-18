#!/usr/bin/env python3
"""Refresh a reviewed, single-vault QMD 2.0.1 index; requires pinned PyYAML.

Install only after initial index/embedding verification and writer drain. All
refresh invocations must use this runner's lock; it cannot lock an unrelated
manual QMD process. Receipts describe this run, not current source completeness.
"""
import argparse
from contextlib import ExitStack
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time


class CleanupUnverified(RuntimeError):
    """A child group may still be running; automatic retries are unsafe."""


def group_stopped(group):
    """Observe the whole group, including descendants whose leader exited."""
    result = subprocess.run(['/bin/ps', '-axo', 'pgid=,stat='],
                            capture_output=True, text=True, timeout=5)
    if result.returncode or not result.stdout.strip():
        raise CleanupUnverified('process-group-observation-failed')
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not fields[0].isdigit():
            raise CleanupUnverified('process-group-observation-invalid')
        if int(fields[0]) == group and not fields[1].startswith('Z'):
            return False
    return True


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def save(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
        temporary = Path(f.name)
    os.replace(temporary, path)


def reviewed_config(config, expected, vault):
    import yaml
    raw = config.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('configuration-hash-changed')
    data = yaml.safe_load(raw)
    cols = data.get('collections') if isinstance(data, dict) else None
    if not isinstance(cols, dict) or len(cols) != 1:
        raise ValueError('exactly-one-reviewed-collection-required')
    name, col = next(iter(cols.items()))
    if not isinstance(name, str) or not isinstance(col, dict):
        raise ValueError('invalid-collection')
    path = col.get('path')
    if not isinstance(path, str) or not path.strip():
        raise ValueError('explicit-collection-path-required')
    if Path(path).resolve() != vault.resolve():
        raise ValueError('collection-vault-mismatch')
    if col.get('update') or col.get('pattern') != '**/*.md':
        raise ValueError('unsupported-scope-or-shell-update')
    return raw, name


def execute(command, env, log, timeout, lock_fd, stderr_log=None):
    with ExitStack() as stack:
        output = stack.enter_context(log.open('wb'))
        errors = stack.enter_context(stderr_log.open('wb')) if stderr_log else subprocess.STDOUT
        process = subprocess.Popen(command, env=env, stdout=output, stderr=errors,
                                   start_new_session=True, pass_fds=(lock_fd,))
        def drain():
            # Cleanup runs outside the signal handler, after Popen.wait has
            # unwound its locks. A repeated TERM must not interrupt that cleanup.
            for number in (signal.SIGTERM, signal.SIGINT):
                signal.signal(number, signal.SIG_IGN)
            def signal_group(number):
                try:
                    os.killpg(process.pid, number)
                except ProcessLookupError:
                    pass
                except PermissionError as exc:
                    # A refused signal is not evidence of termination. Only a
                    # successful group-wide observation can establish that.
                    if not group_stopped(process.pid):
                        raise CleanupUnverified('process-group-signal-denied') from exc
            try:
                signal_group(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                signal_group(signal.SIGKILL)
                process.wait(timeout=5)
                deadline = time.monotonic() + 5
                while not group_stopped(process.pid):
                    if time.monotonic() >= deadline:
                        raise CleanupUnverified('process-group-still-running')
                    time.sleep(0.05)
            except CleanupUnverified:
                raise
            except Exception as exc:
                raise CleanupUnverified('process-group-cleanup-unverified') from exc
        def interrupted(number, _frame):
            raise RuntimeError('runner-interrupted-signal-'+str(number))
        handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
        try:
            for number in handlers:
                signal.signal(number, interrupted)
            try:
                return process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                drain()
                raise RuntimeError('command-timeout')
            except BaseException:
                drain()
                raise
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)


def bound_qmd_command(qmd, node, package_root):
    package_root = package_root.resolve()
    manifest = json.loads((package_root/'package.json').read_text())
    bins = manifest.get('bin') if isinstance(manifest, dict) else None
    if not isinstance(bins, dict) or manifest.get('version') != '2.0.1' or bins.get('qmd') != 'bin/qmd':
        raise ValueError('unsupported-qmd-package-manifest')
    binary = (package_root/'bin/qmd').resolve()
    entry = (package_root/'dist/cli/qmd.js').resolve()
    if (qmd.resolve() != binary or package_root not in binary.parents or
            package_root not in entry.parents or not entry.is_file()):
        raise ValueError('qmd-executable-package-mismatch')
    # The package shell shim chooses Node from PATH (or Bun). Use the explicitly
    # reviewed Node for both CLI and verifier so native module ABI cannot diverge.
    return [str(node.resolve()), str(entry)]


def refresh(vault, config, expected, index, state, qmd, timeout=7200, *, node, package_root, minimum_source_files,
            reconcile_sha256=None):
    vault = vault.resolve()
    if type(minimum_source_files) is not int or minimum_source_files < 1:
        raise ValueError('positive-reviewed-minimum-source-files-required')
    for path in (index, state):
        if path.resolve() == vault or vault in path.resolve().parents:
            raise ValueError('index-and-state-must-be-host-local-outside-vault')
    if not index.is_file() or not qmd.is_absolute() or not qmd.is_file():
        raise ValueError('existing-index-and-absolute-executable-required')
    raw, collection = reviewed_config(config, expected, vault)
    if not node.is_absolute() or not node.is_file() or not package_root.is_absolute() or not (package_root/'package.json').is_file():
        raise ValueError('absolute-node-and-qmd-package-required')
    node = node.resolve()
    package_root = package_root.resolve()
    command = bound_qmd_command(qmd, node, package_root)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state/'refresh.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy', 'as_of': stamp()}, 75
        marker = state/'repair-required.json'
        previous = state/'last-run.json'
        prior_bytes = marker_bytes = None
        if reconcile_sha256 is not None:
            prior_bytes = previous.read_bytes()
            if hashlib.sha256(prior_bytes).hexdigest() != reconcile_sha256:
                raise ValueError('reviewed-recovery-receipt-changed')
            marker_bytes = marker.read_bytes() if marker.exists() else None
        elif marker.exists():
            return {'status': 'repair-required', 'as_of': stamp()}, 2
        if reconcile_sha256 is None and previous.exists():
            try:
                prior = json.loads(previous.read_text())
            except (OSError, ValueError):
                return {'status': 'repair-required', 'reason': 'unreadable-prior-receipt', 'as_of': stamp()}, 2
            if not isinstance(prior, dict) or prior.get('status') not in ('completed', 'failed'):
                return {'status': 'repair-required', 'reason': 'interrupted-or-invalid-prior-run', 'as_of': stamp()}, 2
            if prior.get('cleanup_unverified'):
                return {'status': 'repair-required', 'reason': 'prior-process-cleanup-unverified', 'as_of': stamp()}, 2
            if prior.get('status') == 'failed' and prior.get('failed_stage') not in ('version', 'source-preflight', 'update', 'source-freshness'):
                return {'status': 'repair-required', 'reason': 'prior-embedding-or-verification-failure', 'as_of': stamp()}, 2
        run = Path(tempfile.mkdtemp(prefix='run-', dir=state))
        frozen = run/'config'
        frozen.mkdir()
        (frozen/'index.yml').write_bytes(raw)
        if reconcile_sha256 is not None:
            (run/'prior-last-run.json').write_bytes(prior_bytes)
            if marker_bytes is not None:
                (run/'prior-repair-required.json').write_bytes(marker_bytes)
        env = {key: os.environ[key] for key in ('HOME', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL') if key in os.environ}
        env.update(QMD_CONFIG_DIR=str(frozen), INDEX_PATH=str(index.resolve()))
        receipt = {'status': 'running', 'started_at': stamp(), 'run': str(run),
                   'mode': 'reconcile' if reconcile_sha256 is not None else 'refresh',
                   'reviewed_prior_receipt_sha256': reconcile_sha256,
                   'config_sha256': expected, 'collection': collection, 'steps': [],
                   'minimum_source_files': minimum_source_files,
                   'qmd_command': command, 'qmd_executable': str(qmd.resolve()),
                   'qmd_package': str(package_root.resolve()),
                   'limits': 'Source and chunk coverage are checked at the recorded observation, not guaranteed afterward. Retrieval relevance and factual accuracy are not certified. Ordinary refreshes never clear repair markers. Explicit reconciliation requires a reviewed prior-receipt hash and successful read-only verification.'}
        save(run/'receipt.json', receipt)
        if reconcile_sha256 is None:
            save(state/'last-run.json', receipt)
        stage = 'version'
        try:
            version_log = run/'version.log'
            code = execute(command + ['--version'], env, version_log, 30, lock.fileno())
            if code or not re.fullmatch(r'qmd 2\.0\.1(?: \([0-9a-f]{4,40}\))?', version_log.read_text().strip()):
                raise RuntimeError('unsupported-qmd-version')
            stage = 'source-preflight'
            log = run/'source-preflight.log'
            errors = run/'source-preflight-stderr.log'
            step = {'stage': stage, 'exit': None, 'log': str(log), 'stderr_log': str(errors)}
            receipt['steps'].append(step)
            save(run/'receipt.json', receipt)
            code = execute([str(node), str(Path(__file__).with_name('verify-search-index.mjs')),
                            str(package_root), str(index.resolve()), str(vault), str(frozen/'index.yml'),
                            '--inventory-only'], env, log, timeout, lock.fileno(), stderr_log=errors)
            step['exit'] = code
            inventory = json.loads(log.read_text())
            receipt['source_inventory'] = inventory
            if (code or not isinstance(inventory, dict) or inventory.get('mode') != 'source-inventory' or
                    inventory.get('collection') != collection or inventory.get('config_sha256') != expected or
                    inventory.get('qmd_version') != '2.0.1' or inventory.get('all_checks_pass') is not True or
                    inventory.get('source_issues') != [] or type(inventory.get('source_files')) is not int):
                raise RuntimeError('invalid-or-changing-source-inventory')
            if inventory['source_files'] < minimum_source_files:
                raise RuntimeError('source-count-below-reviewed-minimum')
            for stage in (() if reconcile_sha256 is not None else ('update', 'embed')):
                log = run/(stage+'.log')
                step = {'stage': stage, 'exit': None, 'log': str(log)}
                receipt['steps'].append(step)
                save(run/'receipt.json', receipt)
                code = execute(command + [stage], env, log, timeout, lock.fileno())
                step['exit'] = code
                if code:
                    raise RuntimeError(stage+'-nonzero-exit')
                if stage == 'embed':
                    text = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', log.read_text(errors='replace'))
                    if re.search(r'\b[1-9][0-9]* chunks failed\b', text):
                        raise RuntimeError('embedding-chunks-failed')
                    if not any(s in text for s in ('All content hashes already have embeddings.',
                                                  'No non-empty documents to embed.', 'Done! Embedded')):
                        raise RuntimeError('unrecognized-embedding-result')
            stage = 'verification'
            if config.read_bytes() != raw:
                raise RuntimeError('configuration-changed-during-run')
            log = run/'verification.log'
            errors = run/'verification-stderr.log'
            step = {'stage': stage, 'exit': None, 'log': str(log), 'stderr_log': str(errors)}
            receipt['steps'].append(step)
            save(run/'receipt.json', receipt)
            code = execute([str(node), str(Path(__file__).with_name('verify-search-index.mjs')),
                            str(package_root), str(index.resolve()), str(vault), str(frozen/'index.yml')],
                           env, log, timeout, lock.fileno(), stderr_log=errors)
            step['exit'] = code
            report = json.loads(log.read_text())
            if (not isinstance(report, dict) or report.get('collection') != collection or
                    report.get('config_sha256') != expected or report.get('qmd_version') != '2.0.1' or
                    not isinstance(report.get('source_issues'), list) or not isinstance(report.get('chunk_issues'), list)):
                raise RuntimeError('invalid-full-verification-report')
            receipt['verification'] = report
            if type(report.get('source_files')) is not int or report['source_files'] < minimum_source_files:
                raise RuntimeError('verified-source-count-below-reviewed-minimum')
            if code or report.get('all_checks_pass') is not True or report['source_issues'] or report['chunk_issues']:
                retryable = {'source-content-changed', 'source-not-indexed', 'indexed-source-no-longer-in-scope',
                             'source-changed-during-audit', 'source-paths-changed-during-audit'}
                if (code == 2 and report.get('all_checks_pass') is False and report['source_issues'] and
                        not report['chunk_issues'] and all(isinstance(x, dict) and x.get('reason') in retryable for x in report['source_issues'])):
                    stage = 'source-freshness'
                raise RuntimeError('full-search-verification-failed')
            if reconcile_sha256 is not None:
                stage = 'reconciliation-state'
                if (previous.read_bytes() != prior_bytes or
                        (marker.read_bytes() if marker.exists() else None) != marker_bytes):
                    raise RuntimeError('recovery-state-changed-during-verification')
            receipt.update(status='completed', completed_at=stamp())
            save(run/'receipt.json', receipt)
            if reconcile_sha256 is not None and marker_bytes is not None:
                marker.rename(run/'retired-repair-required.json')
            save(state/'last-success.json', receipt)
            save(state/'last-run.json', receipt)
            return receipt, 0
        except Exception as exc:
            receipt.pop('completed_at', None)
            receipt.update(status='failed', finished_at=stamp(), failed_stage=stage,
                           error=type(exc).__name__+': '+str(exc))
            if isinstance(exc, CleanupUnverified):
                receipt['cleanup_unverified'] = True
            save(run/'receipt.json', receipt)
            if reconcile_sha256 is None:
                save(state/'last-run.json', receipt)
                if stage in ('embed', 'verification') or isinstance(exc, CleanupUnverified):
                    save(state/'repair-required.json', receipt)
            return receipt, 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('vault', 'config', 'index', 'state', 'qmd', 'node', 'package-root'):
        parser.add_argument('--'+flag, type=Path, required=True)
    parser.add_argument('--expected-config-sha256', required=True)
    parser.add_argument('--minimum-source-files', type=int, required=True,
                        help='Reviewed positive source-count floor; never inferred downward from recent runs')
    parser.add_argument('--reconcile-reviewed-receipt-sha256',
                        help='Explicit read-only reconciliation of this reviewed last-run.json hash; never runs update/embed')
    args = parser.parse_args()
    try:
        result, code = refresh(args.vault, args.config, args.expected_config_sha256,
                               args.index, args.state, args.qmd, node=args.node, package_root=args.package_root,
                               minimum_source_files=args.minimum_source_files,
                               reconcile_sha256=args.reconcile_reviewed_receipt_sha256)
    except Exception as exc:
        result, code = {'status': 'preflight-failed', 'error': type(exc).__name__+': '+str(exc)}, 2
    print(json.dumps(result, indent=2))
    raise SystemExit(code)
