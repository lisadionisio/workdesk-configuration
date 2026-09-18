from pathlib import Path
import tempfile, shutil, subprocess, json, datetime, importlib.util, unittest, os
W=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('due',W/'config/scripts/signal-due.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class RuntimeTests(unittest.TestCase):
 def test_weekly_due_on_tuesday(self):self.assertIn('weekly-review',m.due_signals({'weekly-review':{'last-fired':'2026-09-01'}},datetime.date(2026,9,8)))
 def test_never_run_and_null(self):self.assertEqual(len(m.due_signals({'weekly-review':{'last-fired':None}},datetime.date(2026,9,8))),3)
 def test_recent_weekly(self):self.assertNotIn('weekly-review',m.due_signals({'weekly-review':{'last-fired':'2026-09-07'}},datetime.date(2026,9,8)))
 def test_suppressed(self):self.assertNotIn('vault-improvements',m.due_signals({'vault-improvements':{'suppressed-until':'2026-09-09'}},datetime.date(2026,9,8)))
 def test_expiry_boundary(self):self.assertIn('vault-improvements',m.due_signals({'vault-improvements':{'suppressed-until':'2026-09-08'}},datetime.date(2026,9,8)))
 def test_malformed_date(self):self.assertIn('weekly-review',m.due_signals({'weekly-review':{'last-fired':'bad'}},datetime.date(2026,9,8)))
 def test_future_date(self):self.assertIn('weekly-review',m.due_signals({'weekly-review':{'last-fired':'2027-01-01'}},datetime.date(2026,9,8)))
 def test_scans_never_overwrite_shared_state(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);v=root/'vault';scripts=v/'config/scripts';scripts.mkdir(parents=True);state=v/'config/state';state.mkdir();old=state/'session-entry.md';old.write_text('preserved historical scan')
   for name in ['session-entry-scan.sh','signal-due.py']:shutil.copy2(W/'config/scripts'/name,scripts/name)
   for host in ['host-a','host-b']:
    env=dict(os.environ,CLAUDE_PROJECT_DIR=str(v),WORKDESK_STATE_HOME=str(root/host));r=subprocess.run(['bash',str(scripts/'session-entry-scan.sh')],env=env,capture_output=True,text=True)
    self.assertEqual(r.returncode,0,r.stderr);self.assertIn('host-local',json.loads(r.stdout)['hookSpecificOutput']['additionalContext']);self.assertEqual(len(list((root/host).rglob('session-entry.md'))),1)
   self.assertEqual(old.read_text(),'preserved historical scan')
 def test_corrupt_state_visible(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'signals.json';p.write_text('{bad');r=subprocess.run(['python3',str(W/'config/scripts/signal-due.py'),str(p)],capture_output=True,text=True);self.assertEqual(r.returncode,2);self.assertIn('unreadable',r.stderr)
class MigrationIntegrationTests(unittest.TestCase):
 def setup_fixture(self,root):
  vault=root/'vault';cfg=vault/'config';stage=root/'staging';incoming=stage/'workdesk'
  for d in [cfg/'defaults',incoming]:d.mkdir(parents=True)
  for name in ['a.txt','b.txt']:
   (cfg/name).write_text('before');(cfg/'defaults'/name).write_text('before');(incoming/name).write_text('after')
  (cfg/'VERSION').write_text('1.0.0');(cfg/'defaults/VERSION').write_text('1.0.0');(incoming/'VERSION').write_text('1.0.1')
  (stage/'manifest.json').write_text(json.dumps({'version':'1.0.1','migrations':[]}));res=root/'resolutions.json';res.write_text(json.dumps({'b.txt':{'resolution':'theirs'}}))
  import hashlib
  expected=hashlib.sha256(b'before').hexdigest();review={'files':{name:{'operator_sha256':expected} for name in ['a.txt','b.txt']}}
  (stage/'reviewed-plan.json').write_text(json.dumps(review));return vault,cfg,stage,res,review
 def apply(self,vault,stage,res):return subprocess.run(['bash',str(W/'config/scripts/migrate.sh'),'apply',str(stage),str(res)],env=dict(os.environ,CLAUDE_PROJECT_DIR=str(vault)),capture_output=True,text=True)
 def test_concurrent_change_preserved_then_reviewed_retry(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);vault,cfg,stage,res,review=self.setup_fixture(root);(cfg/'b.txt').write_text('other-agent')
   r=self.apply(vault,stage,res);self.assertNotEqual(r.returncode,0);self.assertEqual((cfg/'b.txt').read_text(),'other-agent');self.assertEqual((cfg/'a.txt').read_text(),'after');self.assertEqual((cfg/'VERSION').read_text(),'1.0.0');self.assertTrue(list((vault/'.workdesk-backups').iterdir()))
   import hashlib
   review['files']['b.txt']['operator_sha256']=hashlib.sha256(b'other-agent').hexdigest();(stage/'reviewed-plan.json').write_text(json.dumps(review))
   r=self.apply(vault,stage,res);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual((cfg/'VERSION').read_text().strip(),'1.0.1');self.assertEqual((cfg/'b.txt').read_text(),'after');self.assertEqual(len(list((vault/'.workdesk-backups').iterdir())),2)
 def test_symlink_seen_before_any_copy(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);vault,cfg,stage,res,review=self.setup_fixture(root);(cfg/'b.txt').unlink();external=root/'external';external.write_text('before');(cfg/'b.txt').symlink_to(external)
   r=self.apply(vault,stage,res);self.assertNotEqual(r.returncode,0);self.assertIn('Symlink targets',r.stderr);self.assertEqual((cfg/'a.txt').read_text(),'before');self.assertEqual(external.read_text(),'before')
 def test_old_migration_skipped(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);vault,cfg,stage,res,review=self.setup_fixture(root);m=stage/'migrations';m.mkdir();name='0.9.0-to-1.0.0-obsolete.sh';(m/name).write_text('exit 77\n');(stage/'manifest.json').write_text(json.dumps({'version':'1.0.1','migrations':[name]}))
   r=self.apply(vault,stage,res);self.assertEqual(r.returncode,0,r.stderr)

class CopyTests(unittest.TestCase):
 def test_idempotence_conflict_and_snapshot(self):
  spec=importlib.util.spec_from_file_location('copy_checked',W/'config/scripts/checked-file-copy.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);src=root/'source';dst=root/'target';snap=root/'before';src.write_text('after');dst.write_text('before');old=c.digest(dst)
   self.assertEqual(c.checked_copy(src,dst,old,snap),'applied');self.assertEqual(snap.read_text(),'before')
   self.assertEqual(c.checked_copy(src,dst,old,snap),'no-op')
   dst.write_text('other-agent-edit')
   with self.assertRaises(c.ChangedTarget):c.checked_copy(src,dst,old)
   self.assertEqual(dst.read_text(),'other-agent-edit')
 def test_symlink_target_refused(self):
  spec=importlib.util.spec_from_file_location('copy_checked',W/'config/scripts/checked-file-copy.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);src=root/'source';src.write_text('safe');dst=root/'link';dst.symlink_to(src)
   with self.assertRaises(c.ChangedTarget):c.checked_copy(src,dst,None)

class HealthTests(unittest.TestCase):
 def test_search_summary_distinguishes_defects_from_unknown_coverage(self):
  spec=importlib.util.spec_from_file_location('health',W/'config/scripts/workdesk-health.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
  self.assertEqual(h.search_summary({'state':'not-checked'}),([],['search-not-checked']))
  self.assertEqual(h.search_summary({'state':'unavailable','reason':'index-file-missing'})[0],['search-index-file-missing'])
  for count,pending,expected in [(0,0,['search-empty-collection:test']),(2,1,['search-missing-first-vectors:test']),(2,0,[])]:
   result={'state':'index-observed','collections':[{'name':'test','active_documents':count,'hashes_without_first_vector':pending}]}
   issues,unknown=h.search_summary(result)
   self.assertEqual(issues,expected)
   self.assertIn('search-all-chunk-coverage-unverified',unknown)
 def test_health_top_level_includes_search_failure(self):
  spec=importlib.util.spec_from_file_location('health',W/'config/scripts/workdesk-health.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);result=h.inspect(root,root/'missing.sqlite')
   self.assertEqual(result['status'],'attention')
   self.assertIn('search-index-file-missing',result['exceptions'])
   self.assertFalse((root/'missing.sqlite').exists())
 def test_clean_local_backup_does_not_hide_unchecked_search(self):
  from unittest.mock import patch
  from types import SimpleNamespace
  spec=importlib.util.spec_from_file_location('health',W/'config/scripts/workdesk-health.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
  # Isolate pre-existing backup/safety observations; exercise inspect's actual
  # search integration rather than requiring the operator's local settings.
  def run(args,**kwargs):
   text=''
   if '--format=%ct' in args:text='0'
   elif 'rev-parse' in args and 'HEAD' in args:text='head'
   return SimpleNamespace(returncode=0,stdout=text)
  with tempfile.TemporaryDirectory() as t, patch.object(h.subprocess,'run',side_effect=run), patch.object(h,'backup_status',return_value=[]), patch.object(h,'plugin_status',return_value=(True,True,False,10,[])), patch.object(Path,'is_file',return_value=True):
   result=h.inspect(Path(t))
   self.assertEqual(result['exceptions'],[])
   self.assertEqual(result['status'],'incomplete')
   self.assertEqual(result['unverified'],['search-not-checked'])
 def test_qmd_metadata_missing_foreign_partial_and_complete_first_vectors(self):
  import sqlite3
  spec=importlib.util.spec_from_file_location('health',W/'config/scripts/workdesk-health.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);index=root/'index.sqlite';vault=root/'vault'
   self.assertEqual(h.qmd_index_status(index,vault)['reason'],'index-file-missing')
   self.assertFalse(index.exists())
   db=sqlite3.connect(index)
   db.executescript('CREATE TABLE store_collections(name TEXT,path TEXT,pattern TEXT); CREATE TABLE documents(collection TEXT,hash TEXT,active INTEGER); CREATE TABLE content_vectors(hash TEXT,seq INTEGER);')
   db.execute('INSERT INTO store_collections VALUES(?,?,?)',('test',str(root/'other'),'**/*.md'));db.commit()
   self.assertEqual(h.qmd_index_status(index,vault)['reason'],'vault-collection-missing')
   db.execute('UPDATE store_collections SET path=?',(str(vault),))
   db.executemany('INSERT INTO documents VALUES(?,?,?)',[('test','a',1),('test','a',1),('test','b',1),('test','old',0)])
   db.executemany('INSERT INTO content_vectors VALUES(?,?)',[('a',0),('b',1)]);db.commit()
   before=index.read_bytes();result=h.qmd_index_status(index,vault)
   self.assertEqual(index.read_bytes(),before)
   self.assertEqual(result['collections'][0]['active_documents'],3)
   self.assertEqual(result['collections'][0]['unique_hashes'],2)
   self.assertEqual(result['collections'][0]['hashes_without_first_vector'],1)
   db.execute('INSERT INTO content_vectors VALUES(?,?)',('b',0));db.commit();db.close()
   result=h.qmd_index_status(index,vault)
   self.assertEqual(result['collections'][0]['hashes_without_first_vector'],0)
   self.assertEqual(result['semantic_completeness'],'unverified')
   self.assertEqual(result['freshness'],'unverified')
 def test_qmd_invalid_schema_is_unknown_without_database_mutation(self):
  spec=importlib.util.spec_from_file_location('health',W/'config/scripts/workdesk-health.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
  with tempfile.TemporaryDirectory() as t:
   index=Path(t)/'bad.sqlite';index.write_bytes(b'not a database')
   self.assertEqual(h.qmd_index_status(index,Path(t))['reason'],'index-unreadable-or-unsupported-schema')
   self.assertEqual(index.read_bytes(),b'not a database')
 def test_backup_plugin_states_are_distinguished(self):
  spec=importlib.util.spec_from_file_location('health',W/'config/scripts/workdesk-health.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);plugin=root/'plugins.json';data=root/'data.json'
   cases=[
    (['obsidian-git'],{'autoSaveInterval':10},(True,True,False,10,[])),
    (['obsidian-git'],{'autoSaveInterval':0},(True,False,False,0,['automatic-commit-interval-disabled'])),
    ([],{'autoSaveInterval':10},(False,False,False,10,['automatic-commit-plugin-disabled'])),
    (['obsidian-git'],{'autoSaveInterval':10,'autoPullOnBoot':True},(True,True,True,10,['automatic-pull-enabled'])),
   ]
   for plugins,settings,expected in cases:
    with self.subTest(settings=settings,plugins=plugins):
     plugin.write_text(json.dumps(plugins));data.write_text(json.dumps(settings))
     self.assertEqual(h.plugin_status(plugin,data),expected)
 def test_invalid_backup_settings_report_unknown_without_crashing(self):
  spec=importlib.util.spec_from_file_location('health',W/'config/scripts/workdesk-health.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);plugin=root/'plugins.json';data=root/'data.json'
   cases=[([],[]),({},{}),(['obsidian-git'],{}),(['obsidian-git'],{'autoSaveInterval':'10'}),(['obsidian-git'],{'autoSaveInterval':True}),(['obsidian-git'],{'autoSaveInterval':-1}),(['obsidian-git'],{'autoSaveInterval':float('nan')}),(['obsidian-git'],{'autoSaveInterval':10,'autoPullOnBoot':'false'})]
   for plugins,settings in cases:
    with self.subTest(settings=settings,plugins=plugins):
     plugin.write_text(json.dumps(plugins));data.write_text(json.dumps(settings))
     self.assertEqual(h.plugin_status(plugin,data),(None,None,None,None,['backup-plugin-state-unavailable']))
   data.write_text('{broken')
   self.assertEqual(h.plugin_status(plugin,data)[-1],['backup-plugin-state-unavailable'])
 def test_pending_commit_and_push_thresholds(self):
  spec=importlib.util.spec_from_file_location('health',W/'config/scripts/workdesk-health.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
  self.assertEqual(len(h.backup_status(True,1900,7300,True,False)),2)
  self.assertEqual(h.backup_status(False,9000,None,True,True),[])
  self.assertIn('push-marker-unavailable',h.backup_status(False,0,None,False,False))

class LinkTests(unittest.TestCase):
 def run_case(self, content, notes, expected, require_links=False):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);scripts=root/'config/scripts';scripts.mkdir(parents=True)
   for name in ['check-wikilinks.sh','check-wikilinks.py']:shutil.copy2(W/'config/scripts'/name,scripts/name)
   for name in notes:
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
   source=root/'test.md';source.write_text(content)
   r=subprocess.run(['bash',str(scripts/'check-wikilinks.sh'),*(['--require-links'] if require_links else []),str(source)],capture_output=True,text=True)
   self.assertEqual(r.returncode,expected,r.stdout+r.stderr)
 def test_required_links_reject_plain_path(self):self.run_case('Source: system/intake/source.md',['system/intake/source.md'],1,True)
 def test_required_links_accept_resolved_source(self):self.run_case('Source: [[system/intake/source]]',['system/intake/source.md'],0,True)
 def test_required_links_reject_documentation_only(self):self.run_case('```md\n[[source]]\n```\n`[[source]]`',['source.md'],1,True)
 def test_required_links_reject_legacy_plain_reference(self):self.run_case('`[ACTION] real`',['gtd/inbox/[ACTION] real.md'],1,True)
 def test_raw_source_does_not_require_links_by_default(self):self.run_case('Alex: Here is the source.',[],0)
 def test_historical_target(self):self.run_case('[[system/transcripts/past]]', ['system/transcripts/past.md'],0)
 def test_wrong_path(self):self.run_case('[[wrong/past]]',['atlas/past.md'],1)
 def test_alias_heading_extension(self):self.run_case('[[atlas/past.md#Heading|Alias]]',['atlas/past.md'],0)
 def test_tilde_fence_and_nested_inline(self):self.run_case('~~~md\n[[missing]]\n~~~\n`` `[ACTION] fake` ``',['valid.md'],0)
 def test_real_inbox_reference(self):self.run_case('`[ACTION] real`',['gtd/inbox/[ACTION] real.md'],0)
 def test_ambiguous_basename(self):self.run_case('[[same]]',['atlas/a/same.md','atlas/b/same.md'],1)
 def test_attachment_basename(self):self.run_case('![[diagram.png]]',['system/media/diagram.png'],0)
 def test_missing_signal_state(self):
  r=subprocess.run(['python3',str(W/'config/scripts/signal-due.py'),str(W/'absent-signal-state.json')],capture_output=True,text=True);self.assertEqual(r.returncode,2)
 def test_missing_media(self):self.run_case('![[missing.png]]',[],1)
 def test_missing_input_fails(self):
  r=subprocess.run(['bash',str(W/'config/scripts/check-wikilinks.sh'),str(W/'missing-fixture.md')],capture_output=True,text=True);self.assertEqual(r.returncode,2)

class PrivateOverlayTests(unittest.TestCase):
 def setUp(self):
  spec=importlib.util.spec_from_file_location('overlay',W/'config/scripts/apply-private-config.py');self.overlay=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.overlay)
 def fixture(self,root,product=False):
  vault=root/'vault';config=vault/'config';(config/'scripts').mkdir(parents=True);(config/'defaults').mkdir();(config/'VERSION').write_text('2.3.0');target=config/'scripts/private.sh';target.write_text('before\n');package=root/'package';package.mkdir();source=package/'private.sh';source.write_text('after\n')
  if product:
   (config/'defaults/scripts').mkdir();(config/'defaults/scripts/private.sh').write_text('product\n')
  import hashlib
  row={'target':'scripts/private.sh','source':'private.sh','ownership':'User overrides of product' if product else 'User config','before_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'after_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
  (package/'manifest.json').write_text(json.dumps({'version':'private-1','files':[row]}));return vault,package,target,row
 def test_apply_idempotence_and_per_file_recovery(self):
  with tempfile.TemporaryDirectory() as t:
   vault,pkg,target,row=self.fixture(Path(t));before=(vault/'config/VERSION').read_bytes();defaults=list((vault/'config/defaults').rglob('*'))
   self.assertEqual(self.overlay.run(vault,pkg)['files'][0]['state'],'ready');self.assertEqual(target.read_text(),'before\n')
   result=self.overlay.run(vault,pkg,True);self.assertEqual(target.read_text(),'after\n');self.assertEqual(self.overlay.run(vault,pkg,True)['files'][0]['state'],'no-op');self.assertEqual(len(list((vault/'.workdesk-backups').iterdir())),1)
   self.overlay.restore_file(vault,Path(result['receipt']),row['target']);self.assertEqual(target.read_text(),'before\n');self.assertEqual((vault/'config/VERSION').read_bytes(),before);self.assertEqual(list((vault/'config/defaults').rglob('*')),defaults)
 def test_changed_target_stops_and_preserves(self):
  with tempfile.TemporaryDirectory() as t:
   vault,pkg,target,row=self.fixture(Path(t));target.write_text('other-agent\n')
   with self.assertRaises(ValueError):self.overlay.run(vault,pkg,True)
   self.assertEqual(target.read_text(),'other-agent\n');self.assertFalse((vault/'.workdesk-backups').exists())
 def test_changed_file_prevents_rollback(self):
  with tempfile.TemporaryDirectory() as t:
   vault,pkg,target,row=self.fixture(Path(t));r=self.overlay.run(vault,pkg,True);target.write_text('later-edit\n')
   with self.assertRaises(self.overlay.checked.ChangedTarget):self.overlay.restore_file(vault,Path(r['receipt']),row['target'])
   self.assertEqual(target.read_text(),'later-edit\n')
 def test_scope_and_ownership_enforced(self):
  with tempfile.TemporaryDirectory() as t:
   vault,pkg,target,row=self.fixture(Path(t),True)
   for key in ['../personal/note.md','VERSION','settings.json','state/signals.json','scripts/codex-pre-tool-use-guard.sh','/tmp/escape','.codex/hooks.json']:
    with self.assertRaises(ValueError):self.overlay.target_for(vault/'config',key)
   self.assertEqual(self.overlay.run(vault,pkg)['files'][0]['state'],'ready');row['ownership']='User config';(pkg/'manifest.json').write_text(json.dumps({'version':'private-1','files':[row]}))
   with self.assertRaises(ValueError):self.overlay.run(vault,pkg)
 def test_symlink_parent_escape_stops(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'config').mkdir();(root/'outside').mkdir();(root/'config/scripts').symlink_to(root/'outside',target_is_directory=True)
   with self.assertRaises(ValueError):self.overlay.target_for(root/'config','scripts/private.sh')

class PrivateOverlayEdgeTests(unittest.TestCase):
 setUp=PrivateOverlayTests.setUp
 fixture=PrivateOverlayTests.fixture
 def test_directory_and_alternate_targets_refused_before_writes(self):
  with tempfile.TemporaryDirectory() as t:
   vault,pkg,target,row=self.fixture(Path(t))
   for key in ['', '.', 'scripts/', 'scripts//private.sh']:
    with self.assertRaises(ValueError):self.overlay.target_for(vault/'config',key)
   second=dict(row,target='scripts/PRIVATE.sh');(pkg/'manifest.json').write_text(json.dumps({'version':'private-1','files':[row,second]}))
   with self.assertRaises(ValueError):self.overlay.run(vault,pkg,True)
   directory=vault/'config/scripts/directory';directory.mkdir();second=dict(row,target='scripts/directory',before_sha256=None);(pkg/'manifest.json').write_text(json.dumps({'version':'private-1','files':[row,second]}))
   with self.assertRaises(ValueError):self.overlay.run(vault,pkg,True)
   self.assertEqual(target.read_text(),'before\n');self.assertFalse((vault/'.workdesk-backups').exists())
 def test_partial_failure_reports_receipt_and_retains_concurrent_edit(self):
  from unittest.mock import patch
  import contextlib,io
  with tempfile.TemporaryDirectory() as t:
   vault,pkg,target,row=self.fixture(Path(t));second=vault/'config/scripts/second.sh';second.write_text('before\n');second_row=dict(row,target='scripts/second.sh');(pkg/'manifest.json').write_text(json.dumps({'version':'private-1','files':[row,second_row]}))
   copy=self.overlay.checked.checked_copy
   def interfere(src,dst,expected,backup=None):
    if dst==second:second.write_text('concurrent edit\n')
    return copy(src,dst,expected,backup)
   err=io.StringIO()
   with patch.object(self.overlay.checked,'checked_copy',side_effect=interfere),contextlib.redirect_stderr(err):
    with self.assertRaises(self.overlay.checked.ChangedTarget):self.overlay.run(vault,pkg,True)
   receipts=list((vault/'.workdesk-backups').glob('*/receipt.json'));self.assertEqual(len(receipts),1);self.assertIn(str(receipts[0]),err.getvalue());self.assertEqual(json.loads(receipts[0].read_text())['status'],'partial');self.assertEqual(target.read_text(),'after\n');self.assertEqual(second.read_text(),'concurrent edit\n')
 def test_unsafe_source_mode_and_backup_link_refused(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);vault,pkg,target,row=self.fixture(root);(pkg/'private.sh').chmod(0o666)
   with self.assertRaises(ValueError):self.overlay.run(vault,pkg,True)
   (pkg/'private.sh').chmod(0o644);external=root/'outside';external.mkdir();(vault/'.workdesk-backups').symlink_to(external,target_is_directory=True)
   with self.assertRaises(ValueError):self.overlay.run(vault,pkg,True)
   self.assertEqual(target.read_text(),'before\n');self.assertEqual(list(external.iterdir()),[])

if __name__=='__main__':unittest.main(verbosity=2)
