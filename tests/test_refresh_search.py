from pathlib import Path
from contextlib import closing
import fcntl
import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('refresh_search', ROOT/'config/scripts/refresh-search.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root/'vault'
        self.vault.mkdir()
        self.config = self.root/'index.yml'
        self.data = {'collections': {'fixture': {'path': str(self.vault), 'pattern': '**/*.md'}}}
        self.write_config()
        self.index = self.root/'index.sqlite'
        with closing(sqlite3.connect(self.index)) as db, db:
            db.executescript('CREATE TABLE documents(collection TEXT,hash TEXT,active INTEGER); CREATE TABLE content_vectors(hash TEXT,seq INTEGER); INSERT INTO documents VALUES("fixture","a",1); INSERT INTO content_vectors VALUES("a",0);')
        self.state = self.root/'state'
        self.qmd = self.root/'qmd'
        self.node = self.root/'fake-node'
        self.package_root = self.root/'qmd-package'
        self.package_root.mkdir()
        (self.package_root/'package.json').write_text('{"version":"2.0.1","bin":{"qmd":"bin/qmd"}}')
        (self.package_root/'bin').mkdir()
        (self.package_root/'dist/cli').mkdir(parents=True)
        self.qmd.symlink_to(self.package_root/'bin/qmd')
        self.minimum_source_files=1
        self.fake()
        self.fake_verifier()

    def write_config(self):
        self.config.write_text(json.dumps(self.data))
        self.sha = hashlib.sha256(self.config.read_bytes()).hexdigest()

    def fake(self, mode='success'):
        self.qmd.write_text('#!/usr/bin/env python3\n'
            'import sys,os,json,time\nfrom pathlib import Path\n'
            f'mode={mode!r}\n'
            'stage=sys.argv[1]\n'
            'if stage=="--version":\n print("qmd 2.0.1 (abc1234)" if mode=="version-suffix" else "qmd 2.0.2" if mode=="wrong-version" else "qmd 2.0.1");sys.exit(0)\n'
            'config=Path(os.environ["QMD_CONFIG_DIR"])/"index.yml"\n'
            'assert json.loads(config.read_text())["collections"]["fixture"].get("update") is None\n'
            'if stage=="embed":\n'
            ' if mode=="timeout": time.sleep(30)\n'
            ' if mode=="partial": print("✓ Done! Embedded 1 chunks from 1 documents");print("⚠ 1 chunks failed")\n'
            ' elif mode=="unknown": print("unexpected response")\n'
            ' else: print("✓ All content hashes already have embeddings.")\n'
            'sys.exit(3 if mode=="update-failure" and stage=="update" else 0)\n')
        self.qmd.chmod(0o700)
        (self.package_root/'dist/cli/qmd.js').write_text(self.qmd.read_text())

    def fake_verifier(self, mode='success', source_count=1, inventory_mode='success'):
        self.node.write_text('#!/usr/bin/env python3\n'
            'import json,sys,hashlib,os\nfrom pathlib import Path\n'
            'if sys.argv[1].endswith("/dist/cli/qmd.js"): os.execv(sys.executable,[sys.executable,*sys.argv[1:]])\n'
            f'mode={mode!r}\n'
            f'source_count={source_count!r}\ninventory_mode={inventory_mode!r}\n'
            'if sys.argv[-1]=="--inventory-only":\n'
            ' raw=Path(sys.argv[-2]).read_bytes()\n'
            ' issues=[{"reason":"source-changed-during-audit"}] if inventory_mode=="changing" else []\n'
            ' print(json.dumps({"mode":"source-inventory","collection":"fixture","qmd_version":"2.0.1","config_sha256":hashlib.sha256(raw).hexdigest(),"source_files":source_count,"source_issues":issues,"all_checks_pass":not issues}))\n'
            ' sys.exit(2 if issues else 0)\n'
            'if mode=="malformed": print("not JSON");sys.exit(0)\n'
            f'if mode=="state-change": Path({str(self.state/"last-run.json")!r}).write_text("newer owner receipt")\n'
            'if mode=="stderr-warning": print("synthetic Node warning",file=sys.stderr)\n'
            'raw=Path(sys.argv[-1]).read_bytes()\n'
            'source=[{"reason":"source-content-changed"}] if mode=="stale" else []\n'
            'chunks=[{"reason":"missing-chunk"}] if mode=="missing" else []\n'
            'print(json.dumps({"collection":"fixture","qmd_version":"2.0.1","config_sha256":hashlib.sha256(raw).hexdigest(),"source_files":source_count,"source_issues":source,"chunk_issues":chunks,"all_checks_pass":not(source or chunks)}))\n'
            'sys.exit(2 if source or chunks else 0)\n')
        self.node.chmod(0o700)

    def run_refresh(self, timeout=10, reconcile_sha256=None):
        return runner.refresh(self.vault,self.config,self.sha,self.index,self.state,self.qmd,timeout,node=self.node,package_root=self.package_root,minimum_source_files=self.minimum_source_files,reconcile_sha256=reconcile_sha256)

    def test_success_keeps_frozen_configuration_and_receipt(self):
        before=self.config.read_bytes()
        receipt,code=self.run_refresh()
        self.assertEqual(code,0,receipt)
        self.assertEqual([s['stage'] for s in receipt['steps']],['source-preflight','update','embed','verification'])
        self.assertEqual((Path(receipt['run'])/'config/index.yml').read_bytes(),before)
        self.assertEqual(self.config.read_bytes(),before)
        self.assertEqual(json.loads((self.state/'last-success.json').read_text()),receipt)

    def test_changed_config_and_custom_commands_refused_before_execution(self):
        self.config.write_text('{}')
        with self.assertRaisesRegex(ValueError,'hash-changed'):self.run_refresh()
        self.assertFalse(self.state.exists())
        self.data['collections']['fixture']['update']='echo unexpected'
        self.write_config()
        with self.assertRaisesRegex(ValueError,'shell-update'):self.run_refresh()
        self.assertFalse(self.state.exists())

    def test_supported_version_git_suffix_and_unsupported_version(self):
        self.fake('version-suffix');receipt,code=self.run_refresh()
        self.assertEqual(code,0,receipt)
        self.fake('wrong-version');receipt,code=self.run_refresh()
        self.assertEqual(code,2)
        self.assertEqual(receipt['steps'],[])
        self.assertIn('unsupported-qmd-version',receipt['error'])

    def test_cli_and_verifier_use_reviewed_runtime_and_package(self):
        receipt,code=self.run_refresh()
        self.assertEqual(code,0,receipt)
        self.assertEqual(receipt['qmd_command'],[str(self.node.resolve()),str((self.package_root/'dist/cli/qmd.js').resolve())])
        self.assertEqual(receipt['qmd_executable'],str(self.qmd.resolve()))

    def test_other_same_version_executable_refused_before_state_or_index_changes(self):
        other=self.root/'other-qmd'
        other.write_text(self.qmd.read_text());other.chmod(0o700)
        self.qmd=other
        before=self.index.read_bytes()
        with self.assertRaisesRegex(ValueError,'executable-package-mismatch'):self.run_refresh()
        self.assertFalse(self.state.exists())
        self.assertEqual(self.index.read_bytes(),before)

    def test_other_vault_and_multiple_collections_refused(self):
        self.data['collections']['fixture']['path']=str(self.root/'other')
        self.write_config()
        with self.assertRaisesRegex(ValueError,'vault-mismatch'):self.run_refresh()
        self.data['collections']['second']=self.data['collections']['fixture'].copy()
        self.write_config()
        with self.assertRaisesRegex(ValueError,'one-reviewed'):self.run_refresh()

    def test_missing_collection_path_rejected_even_when_cwd_is_vault(self):
        import os
        self.data['collections']['fixture'].pop('path');self.write_config()
        previous=Path.cwd()
        try:
            os.chdir(self.vault)
            with self.assertRaisesRegex(ValueError,'explicit-collection-path'):self.run_refresh()
        finally:os.chdir(previous)

    def test_busy_does_not_replace_last_success(self):
        self.run_refresh();before=(self.state/'last-success.json').read_bytes()
        with (self.state/'refresh.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            receipt,code=self.run_refresh()
        self.assertEqual((receipt['status'],code),('busy',75))
        self.assertEqual((self.state/'last-success.json').read_bytes(),before)

    def test_partial_embedding_zero_exit_requires_repair_and_preserves_success(self):
        self.run_refresh();before=(self.state/'last-success.json').read_bytes()
        self.fake('partial');receipt,code=self.run_refresh()
        self.assertEqual(code,2)
        self.assertIn('chunks-failed',receipt['error'])
        self.assertTrue((self.state/'repair-required.json').exists())
        self.fake();receipt,code=self.run_refresh()
        self.assertEqual((receipt['status'],code),('repair-required',2))
        self.assertEqual((self.state/'last-success.json').read_bytes(),before)

    def test_update_failure_stops_before_embedding(self):
        self.fake('update-failure');receipt,code=self.run_refresh()
        self.assertEqual(code,2)
        self.assertEqual([s['stage'] for s in receipt['steps']],['source-preflight','update'])
        self.assertFalse((self.state/'last-success.json').exists())

    def test_interrupted_or_unreadable_prior_receipt_blocks_retry(self):
        self.state.mkdir()
        for previous in ['{"status":"running"}', '{bad', '[]']:
            with self.subTest(previous=previous):
                (self.state/'last-run.json').write_text(previous)
                receipt,code=self.run_refresh()
                self.assertEqual((receipt['status'],code),('repair-required',2))
                self.assertFalse(list(self.state.glob('run-*')))

    def test_full_verifier_missing_chunk_blocks_success_despite_zero_cli_backlog(self):
        self.fake_verifier('missing');receipt,code=self.run_refresh()
        self.assertEqual(code,2)
        self.assertEqual(receipt['failed_stage'],'verification')
        self.assertTrue((self.state/'repair-required.json').exists())
        self.assertFalse((self.state/'last-success.json').exists())

    def test_source_only_staleness_can_retry_without_waiving_verification(self):
        self.fake_verifier('stale');receipt,code=self.run_refresh()
        self.assertEqual((code,receipt['failed_stage']),(2,'source-freshness'))
        self.assertFalse((self.state/'repair-required.json').exists())
        self.assertFalse((self.state/'last-success.json').exists())
        self.fake_verifier();receipt,code=self.run_refresh()
        self.assertEqual(code,0)
        self.assertTrue(receipt['verification']['all_checks_pass'])

    def test_malformed_verifier_output_is_not_success(self):
        self.fake_verifier('malformed');receipt,code=self.run_refresh()
        self.assertEqual(code,2)
        self.assertTrue((self.state/'repair-required.json').exists())

    def test_verifier_stderr_is_preserved_separately_from_whole_json(self):
        self.fake_verifier('stderr-warning');receipt,code=self.run_refresh()
        self.assertEqual(code,0,receipt)
        step=receipt['steps'][-1]
        self.assertEqual(Path(step['stderr_log']).read_text(),'synthetic Node warning\n')
        self.assertTrue(json.loads(Path(step['log']).read_text())['all_checks_pass'])
        self.assertFalse((self.state/'repair-required.json').exists())

    def test_empty_or_partial_sources_stop_before_update_and_preserve_last_success(self):
        self.minimum_source_files=4
        self.fake_verifier(source_count=4)
        receipt,code=self.run_refresh();self.assertEqual(code,0,receipt)
        before=(self.state/'last-success.json').read_bytes()
        for count in (0,3,3):
            with self.subTest(count=count):
                self.fake_verifier(source_count=count)
                receipt,code=self.run_refresh()
                self.assertEqual((code,receipt['failed_stage']),(2,'source-preflight'))
                self.assertEqual([s['stage'] for s in receipt['steps']],['source-preflight'])
                self.assertIn('source-count-below-reviewed-minimum',receipt['error'])
                self.assertEqual((self.state/'last-success.json').read_bytes(),before)
                self.assertFalse((self.state/'repair-required.json').exists())
        self.fake_verifier(source_count=4)
        receipt,code=self.run_refresh();self.assertEqual(code,0,receipt)

    def test_source_inventory_changes_stop_before_update(self):
        self.fake_verifier(inventory_mode='changing')
        receipt,code=self.run_refresh()
        self.assertEqual((code,receipt['failed_stage']),(2,'source-preflight'))
        self.assertEqual([s['stage'] for s in receipt['steps']],['source-preflight'])

    def test_source_minimum_must_be_reviewed_positive_integer(self):
        for minimum in (0,-1,True,'1'):
            self.minimum_source_files=minimum
            with self.assertRaisesRegex(ValueError,'positive-reviewed-minimum'):self.run_refresh()
        self.assertFalse(self.state.exists())

    def failed_state(self):
        receipt,code=self.run_refresh();self.assertEqual(code,0,receipt)
        self.fake('partial');receipt,code=self.run_refresh();self.assertEqual(code,2)
        prior=(self.state/'last-run.json').read_bytes()
        marker=(self.state/'repair-required.json').read_bytes()
        success=(self.state/'last-success.json').read_bytes()
        return prior,marker,success,hashlib.sha256(prior).hexdigest()

    def test_reconciliation_preserves_failure_and_verifies_without_update_or_embed(self):
        prior,marker,_,digest=self.failed_state()
        before=self.index.read_bytes()
        receipt,code=self.run_refresh(reconcile_sha256=digest)
        self.assertEqual(code,0,receipt)
        self.assertEqual(receipt['mode'],'reconcile')
        self.assertEqual([s['stage'] for s in receipt['steps']],['source-preflight','verification'])
        self.assertEqual((Path(receipt['run'])/'prior-last-run.json').read_bytes(),prior)
        self.assertEqual((Path(receipt['run'])/'retired-repair-required.json').read_bytes(),marker)
        self.assertEqual(self.index.read_bytes(),before)
        self.assertFalse((self.state/'repair-required.json').exists())
        self.fake();receipt,code=self.run_refresh();self.assertEqual(code,0,receipt)

    def test_failed_reconciliation_preserves_blockers_and_last_success(self):
        prior,marker,success,digest=self.failed_state()
        for mode in ('missing','stale','malformed'):
            with self.subTest(mode=mode):
                self.fake_verifier(mode)
                receipt,code=self.run_refresh(reconcile_sha256=digest)
                self.assertEqual(code,2,receipt)
                self.assertEqual((self.state/'last-run.json').read_bytes(),prior)
                self.assertEqual((self.state/'repair-required.json').read_bytes(),marker)
                self.assertEqual((self.state/'last-success.json').read_bytes(),success)
                self.assertEqual([s['stage'] for s in receipt['steps']],['source-preflight','verification'])

    def test_reconciliation_refuses_unreviewed_receipt_and_preserves_concurrent_state(self):
        prior,marker,success,digest=self.failed_state()
        with self.assertRaisesRegex(ValueError,'reviewed-recovery-receipt-changed'):
            self.run_refresh(reconcile_sha256='0'*64)
        self.assertEqual((self.state/'last-run.json').read_bytes(),prior)
        self.fake_verifier('state-change')
        receipt,code=self.run_refresh(reconcile_sha256=digest)
        self.assertEqual(code,2,receipt)
        self.assertIn('recovery-state-changed',receipt['error'])
        self.assertEqual((self.state/'last-run.json').read_text(),'newer owner receipt')
        self.assertEqual((self.state/'repair-required.json').read_bytes(),marker)
        self.assertEqual((self.state/'last-success.json').read_bytes(),success)

    def test_reconciliation_recovers_after_failure_between_marker_retirement_and_state_commit(self):
        prior,marker,success,digest=self.failed_state()
        original_save=runner.save
        def failing_save(path,value):
            if path==self.state/'last-success.json':raise OSError('simulated state commit failure')
            original_save(path,value)
        with patch.object(runner,'save',side_effect=failing_save):
            failed,code=self.run_refresh(reconcile_sha256=digest)
        self.assertEqual(code,2,failed)
        self.assertNotIn('completed_at',failed)
        self.assertEqual((self.state/'last-run.json').read_bytes(),prior)
        self.assertEqual((self.state/'last-success.json').read_bytes(),success)
        self.assertEqual((Path(failed['run'])/'retired-repair-required.json').read_bytes(),marker)
        self.assertFalse((self.state/'repair-required.json').exists())
        blocked,code=self.run_refresh()
        self.assertEqual((code,blocked['status']),(2,'repair-required'))
        recovered,code=self.run_refresh(reconcile_sha256=digest)
        self.assertEqual(code,0,recovered)
        self.assertEqual(recovered['mode'],'reconcile')
        self.assertEqual((Path(recovered['run'])/'prior-last-run.json').read_bytes(),prior)

    def test_failure_receipt_blocks_retry_when_repair_marker_was_not_written(self):
        self.state.mkdir()
        for stage in ['embed','verification',None]:
            with self.subTest(stage=stage):
                (self.state/'last-run.json').write_text(json.dumps({'status':'failed','failed_stage':stage}))
                receipt,code=self.run_refresh()
                self.assertEqual((receipt['status'],code),('repair-required',2))
                self.assertFalse(list(self.state.glob('run-*')))

    def test_timeout_drains_descendant_before_return(self):
        import os
        import sys
        import time
        child_output=self.root/'late-child-output'
        child_ready=self.root/'timeout-child-ready'
        child_code='import time;from pathlib import Path;Path('+repr(str(child_ready))+').write_text("ready");time.sleep(1.3);Path('+repr(str(child_output))+').write_text("late")'
        parent_code='import subprocess,time,sys;subprocess.Popen([sys.executable,"-c",'+repr(child_code)+']);time.sleep(30)'
        with (self.root/'test.lock').open('a') as lock:
            with self.assertRaisesRegex(RuntimeError,'timeout'):
                runner.execute([sys.executable,'-c',parent_code],dict(os.environ),self.root/'timeout.log',1,lock.fileno())
        self.assertTrue(child_ready.exists(),'Child must start before timeout is tested')
        time.sleep(1.4)
        self.assertFalse(child_output.exists())

    def test_group_observation_requires_no_live_members(self):
        from subprocess import CompletedProcess
        for output, stopped in [('42 Z\n43 S\n', True), ('42 S\n42 Z\n', False),
                                ('43 R\n', True)]:
            with patch.object(runner.subprocess, 'run', return_value=CompletedProcess([], 0, output, '')):
                self.assertEqual(runner.group_stopped(42), stopped)
        for code, output in [(1, '43 S\n'), (0, ''), (0, 'bad output row\n')]:
            with patch.object(runner.subprocess, 'run', return_value=CompletedProcess([], code, output, '')):
                with self.assertRaises(runner.CleanupUnverified):runner.group_stopped(42)

    def test_permission_denied_requires_independent_group_observation(self):
        import os
        import subprocess
        for stopped in [False, True]:
            process = Mock(pid=424242)
            process.wait.side_effect = [subprocess.TimeoutExpired(['fixture'], 1), 0, 0]
            with (self.root/'test.lock').open('a') as lock:
                with patch.object(runner.subprocess, 'Popen', return_value=process), \
                     patch.object(runner.os, 'killpg', side_effect=PermissionError('fixture denial')), \
                     patch.object(runner, 'group_stopped', return_value=stopped) as observe:
                    expected = RuntimeError if stopped else runner.CleanupUnverified
                    message = '^command-timeout$' if stopped else 'signal-denied'
                    with self.assertRaisesRegex(expected, message):
                        runner.execute(['fixture'], dict(os.environ), self.root/'denied.log', 1, lock.fileno())
                    observe.assert_called_with(process.pid)

    def test_unverified_cleanup_blocks_retry_even_before_embedding(self):
        with patch.object(runner, 'execute', side_effect=runner.CleanupUnverified('fixture')):
            receipt, code = self.run_refresh()
        self.assertEqual((code, receipt['failed_stage']), (2, 'version'))
        self.assertTrue(receipt['cleanup_unverified'])
        self.assertTrue((self.state/'repair-required.json').exists())
        self.assertFalse((self.state/'last-success.json').exists())
        (self.state/'repair-required.json').unlink()
        with patch.object(runner, 'execute') as execute:
            blocked, code = self.run_refresh()
        self.assertEqual((code, blocked['status']), (2, 'repair-required'))
        execute.assert_not_called()

    def test_runner_term_drains_started_child_that_ignores_term(self):
        import os
        import subprocess
        import sys
        import time
        ready=self.root/'child-ready';late=self.root/'child-late'
        child='import signal,time,os;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path('+repr(str(ready))+').write_text(str(os.getpid()));time.sleep(30);Path('+repr(str(late))+').write_text("late")'
        parent='import importlib.util,os,sys;from pathlib import Path;spec=importlib.util.spec_from_file_location("r",'+repr(str(ROOT/'config/scripts/refresh-search.py'))+');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r);lock=open('+repr(str(self.root/'signal.lock'))+',"a");r.execute([sys.executable,"-c",'+repr(child)+'],dict(os.environ),Path('+repr(str(self.root/'signal.log'))+'),30,lock.fileno())'
        with (self.root/'parent.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,'-c',parent],stdout=log,stderr=log)
            try:
                deadline=time.monotonic()+5
                while not ready.exists() and time.monotonic()<deadline and process.poll() is None:time.sleep(0.01)
                self.assertTrue(ready.exists(),'Child must start before interruption is tested')
                process.terminate();process.wait(timeout=15)
                with self.assertRaises(ProcessLookupError):os.kill(int(ready.read_text()),0)
                self.assertFalse(late.exists())
                self.assertNotEqual(process.returncode,0)
            finally:
                if process.poll() is None:process.terminate();process.wait(timeout=15)

    def test_unknown_output_and_timeout_require_repair(self):
        for mode in ['unknown','timeout']:
            with self.subTest(mode=mode):
                self.state=self.root/('state-'+mode)
                self.fake(mode);receipt,code=self.run_refresh(timeout=1)
                self.assertEqual(code,2)
                self.assertTrue((self.state/'repair-required.json').exists())


if __name__=='__main__':unittest.main()
