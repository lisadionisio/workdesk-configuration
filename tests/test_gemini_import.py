"""Run the Gemini importer against synthetic Docs responses in a temporary vault."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class GeminiImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workdesk-gemini-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root/'vault'
        scripts = self.vault/'config/scripts'
        (scripts/'lib').mkdir(parents=True)
        for name in ['pull-gemini-transcripts.sh','lib/gws-layout.sh','lib/gws_account.py','lib/gemini_source_identity.py','lib/gemini_document_text.py','lib/gemini_import_lock.py']:
            shutil.copy2(ROOT/'config/scripts'/name,scripts/name)
        self.script = scripts/'pull-gemini-transcripts.sh'
        (self.vault/'system/transcripts').mkdir(parents=True)
        home=self.root/'home';state=home/'.config/gws';state.mkdir(parents=True)
        for name in ['credentials.enc','client_secret.json']:(state/name).write_text('synthetic')
        self.bin=self.root/'bin';self.bin.mkdir()
        gws=self.bin/'gws'
        gws.write_text('#!'+sys.executable+'\n'+'''import json,os,sys
from pathlib import Path
a=sys.argv[1:]
account=os.environ.get('GOOGLE_WORKSPACE_CLI_ACCOUNT') or Path(os.environ['GOOGLE_WORKSPACE_CLI_CONFIG_DIR']).name
assert 'GOOGLE_WORKSPACE_CLI_TOKEN' not in os.environ
with open(os.environ['CALLS'],'a') as f:f.write(json.dumps({'account':account,'args':a})+'\\n')
if a==['--version']:print('gws '+os.environ.get('FAKE_VERSION','0.22.5'))
elif a[:3]==['drive','about','get']:print(json.dumps({'user':{'emailAddress':'wrong@example.test' if os.environ.get('WRONG_IDENTITY') else account}}))
elif a[:3]==['calendar','events','list']:
 p=json.loads(a[a.index('--params')+1]);mode=os.environ.get('CALENDAR_MODE','empty');second='pageToken' in p
 if mode=='page-fail' and second:sys.exit(8)
 if mode=='malformed':print('{"items":{}}');sys.exit(0)
 if mode=='bad-token':print(json.dumps({'items':[],'nextPageToken':'bad\\nvalue'}));sys.exit(0)
 data={'items':[]}
 if mode=='metadata':data['items']=[json.loads(os.environ['CALENDAR_EVENT'])]
 if mode=='page-fail' and not second:
  data['items']=[{'summary':'Must not export before page two','start':{'dateTime':'2026-09-09T14:00:00Z'},'attachments':[{'title':'Notes by Gemini','fileId':'fixture-doc'}]}]
 if mode in ['pages','page-fail','repeated']:
  if not second or mode=='repeated':data['nextPageToken']='next'
 print(json.dumps(data))
elif a[:3]==['docs','documents','get']:
 p=json.loads(a[a.index('--params')+1])
 if p.get('includeTabsContent'):
  print(Path(os.environ['DOC_FIXTURE']).read_text());sys.exit(int(os.environ.get('DOC_EXIT','0')))
 else:
  mode=os.environ.get('META_MODE','normal')
  print(json.dumps({'documentId':'wrong-doc' if mode=='wrong-id' else 'fixture-doc','title':[] if mode=='bad-title' else 'Fixture'}))
  sys.exit(8 if mode=='failed' else 0)
else:sys.exit(90)
''');gws.chmod(0o700)
        self.doc=self.root/'doc.json'
        self.calls=self.root/'calls'
        self.state=self.root/'state'
        routes={}
        for account in ['fixture@example.test','second@example.test']:
            store=self.root/account;store.mkdir();routes[account]={'mode':'config-dir','config_dir':str(store)}
        self.env=dict(os.environ,HOME=str(home),XDG_CONFIG_HOME=str(home/'.config'),PATH=str(self.bin)+':'+os.environ['PATH'],DOC_FIXTURE=str(self.doc),
            WORKDESK_PYTHON=sys.executable,WORKDESK_GWS_BIN=str(gws),WORKDESK_STATE_HOME=str(self.state),WORKDESK_GWS_ACCOUNTS=json.dumps(routes),CALLS=str(self.calls),GOOGLE_WORKSPACE_CLI_TOKEN='synthetic-conflicting-token')
        self.env.pop('WORKDESK_GWS_ACCOUNT',None)
        self.env.pop('WORKDESK_GEMINI_LOCK_FD',None)
        self.legacy=self.vault/'config/state/pull-gemini.json'
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_text('{"last_success_at":"2099-01-01T00:00:00Z"}')
        self.checkpoint=self.checkpoint_for('fixture@example.test')
        self.checkpoint.parent.mkdir(parents=True)
        self.before=b'{"account":"fixture@example.test","last_success_at":"2026-09-01T00:00:00Z"}\n'
        self.checkpoint.write_bytes(self.before)

    def checkpoint_for(self,account):
        vault=hashlib.sha256(str(self.vault).encode()).hexdigest()[:16]
        principal=hashlib.sha256(account.encode()).hexdigest()[:32]
        return self.state/vault/'gemini-transcripts'/principal/'pull-gemini.json'

    def enumeration(self,account='fixture@example.test',args=(),extra=None):
        cmd=['bash',str(self.script)]
        if account is not None:cmd+=['--account',account]
        return subprocess.run(cmd+list(args),env=dict(self.env,**(extra or {})),capture_output=True,text=True,timeout=20)

    def test_requires_explicit_account_before_provider_or_state_writes(self):
        r=self.enumeration(None);self.assertEqual(r.returncode,2)
        self.assertFalse(self.calls.exists());self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_account_checkpoints_are_separate_and_legacy_ignored(self):
        legacy=self.legacy.read_bytes()
        for account in ['fixture@example.test','second@example.test']:
            r=self.enumeration(account);self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(json.loads(self.checkpoint_for(account).read_text())['account'],account)
        self.assertEqual(self.legacy.read_bytes(),legacy)
        calls=[json.loads(x) for x in self.calls.read_text().splitlines()]
        self.assertEqual({c['account'] for c in calls},{'fixture@example.test','second@example.test'})

    def test_wrong_identity_stops_before_calendar_and_retains_success(self):
        r=self.enumeration(extra={'WRONG_IDENTITY':'1'});self.assertEqual(r.returncode,2,r.stderr)
        self.assertEqual(json.loads(self.checkpoint.read_text())['last_success_at'],'2026-09-01T00:00:00Z')
        calls=[json.loads(x) for x in self.calls.read_text().splitlines()]
        self.assertFalse(any(c['args'][0]=='calendar' for c in calls))

    def test_rejects_mismatched_checkpoint_before_provider(self):
        self.checkpoint.write_text('{"account":"second@example.test"}')
        r=self.enumeration();self.assertEqual(r.returncode,2);self.assertFalse(self.calls.exists())
        self.assertEqual(self.checkpoint.read_text(),'{"account":"second@example.test"}')

    def test_status_uses_account_checkpoint_without_provider(self):
        r=self.enumeration(args=['--status']);self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('fixture@example.test',r.stdout);self.assertFalse(self.calls.exists())

    def test_dry_run_auth_failure_keeps_checkpoint(self):
        r=self.enumeration(args=['--dry-run'],extra={'WRONG_IDENTITY':'1'})
        self.assertEqual(r.returncode,2);self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_legacy_account_version_uses_the_same_identity_contract(self):
        routes={'fixture@example.test':{'mode':'legacy-account'}}
        r=self.enumeration(extra={'FAKE_VERSION':'0.4.1','WORKDESK_GWS_ACCOUNTS':json.dumps(routes)})
        self.assertEqual(r.returncode,0,r.stderr)

    def test_bad_arguments_fail_before_provider(self):
        for args in [['--days'],['--days','0'],['--doc-id'],['--doc-id','../bad'],['--account','second@example.test']]:
            with self.subTest(args=args):
                r=self.enumeration(args=args);self.assertEqual(r.returncode,2);self.assertFalse(self.calls.exists())

    def test_pagination_finishes_before_success(self):
        r=self.enumeration(extra={'CALENDAR_MODE':'pages'});self.assertEqual(r.returncode,0,r.stderr)
        calls=[json.loads(x) for x in self.calls.read_text().splitlines()]
        pages=[c for c in calls if c['args'][:3]==['calendar','events','list']]
        self.assertEqual(len(pages),2)
        self.assertEqual(json.loads(pages[1]['args'][pages[1]['args'].index('--params')+1])['pageToken'],'next')

    def test_page_errors_and_cycles_preserve_prior_success(self):
        for mode in ['page-fail','malformed','bad-token','repeated']:
            with self.subTest(mode=mode):
                r=self.enumeration(extra={'CALENDAR_MODE':mode});self.assertEqual(r.returncode,2,r.stderr)
                self.assertEqual(json.loads(self.checkpoint.read_text())['last_success_at'],'2026-09-01T00:00:00Z')
                self.assertEqual(self.notes(),[])
                calls=[json.loads(x) for x in self.calls.read_text().splitlines()]
                self.assertFalse(any(c['args'][:3]==['docs','documents','get'] for c in calls))

    def document(self, chunks):
        self.doc.write_text(json.dumps({'documentId':'fixture-doc','tabs':[
            {'tabProperties':{'title':'Notes'},'documentTab':{'body':{'content':[{'paragraph':{'elements':[{'textRun':{'content':'AI SUMMARY MUST NOT BE IMPORTED'}}]}}]}}},
            {'tabProperties':{'title':'Transcript'},'documentTab':{'body':{'content':[{'paragraph':{'elements':[{'textRun':{'content':c}} for c in chunks]}}]}}}
        ]}))

    def run_import(self,*args):
        return subprocess.run(['bash',str(self.script),'--account','fixture@example.test','--doc-id','fixture-doc','--force',*args],env=self.env,capture_output=True,text=True,timeout=20)

    def notes(self):return list((self.vault/'system/intake').glob('*.md'))

    def test_exact_speaker_turns_blank_lines_unicode_and_trailing_newlines(self):
        chunks=['Alex: Café — keep this turn.\n\n','Robin: Preserve every newline.\n'*30,'\n\n']
        self.document(chunks);r=self.run_import();self.assertEqual(r.returncode,0,r.stderr)
        body=self.notes()[0].read_bytes().split(b'## Transcript\n\n',1)[1]
        self.assertEqual(body,''.join(chunks).encode())
        self.assertNotIn(b'AI SUMMARY',body)
        self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_dry_run_preserves_checkpoint_and_publishes_nothing(self):
        self.document(['Alex: fixture line.\n'*60]);r=self.run_import('--dry-run')
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(self.notes(),[])
        self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_malformed_document_is_a_failure_without_checkpoint_advance(self):
        self.doc.write_text('{invalid');r=self.run_import()
        self.assertNotEqual(r.returncode,0);self.assertEqual(self.notes(),[])
        self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_single_doc_denied_is_not_reported_as_success(self):
        self.doc.write_text('{"error":{"code":403}}');r=self.run_import()
        self.assertNotEqual(r.returncode,0);self.assertEqual(self.notes(),[])
        self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_force_cannot_replace_existing_source(self):
        self.document(['Alex: original source.\n'*60]);r=self.run_import();self.assertEqual(r.returncode,0,r.stderr)
        note=self.notes()[0];before=note.read_bytes()
        self.document(['Robin: changed source.\n'*60]);r=self.run_import()
        self.assertNotEqual(r.returncode,0);self.assertEqual(note.read_bytes(),before)
        self.assertEqual(len(self.notes()),1)

    def test_existing_suffix_collision_is_preserved(self):
        import datetime
        date='undated'
        intake=self.vault/'system/intake';intake.mkdir(exist_ok=True)
        occupied=[intake/(date+'-fixture.md'),intake/(date+'-fixture-fixtur.md')]
        for p in occupied:p.write_text('Existing operator source: '+p.name)
        before={p:p.read_bytes() for p in occupied}
        self.document(['Alex: source collision.\n'*60]);r=self.run_import()
        self.assertNotEqual(r.returncode,0)
        for p,data in before.items():self.assertEqual(p.read_bytes(),data)

    def test_forced_archived_source_cannot_be_duplicated(self):
        self.document(['Alex: archived source.\n'*60]);r=self.run_import();self.assertEqual(r.returncode,0,r.stderr)
        note=self.notes()[0];archived=self.vault/'system/transcripts'/note.name;note.rename(archived);before=archived.read_bytes()
        r=self.run_import();self.assertNotEqual(r.returncode,0)
        self.assertEqual(archived.read_bytes(),before);self.assertEqual(self.notes(),[])

    def test_body_mention_is_not_a_source_identity(self):
        other=self.vault/'system/transcripts/other.md'
        other.write_text('---\ngemini-doc-id: other-document\n---\nMention in transcript:\ngemini-doc-id: fixture-doc\n')
        self.document(['Alex: new source.\n'*60]);r=self.run_import()
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(len(self.notes()),1)

    def test_quoted_source_identity_is_recognized(self):
        other=self.vault/'system/transcripts/other.md'
        for value in ['"fixture-doc"', "'fixture-doc'"]:
            with self.subTest(value=value):
                other.write_text('---\ngemini-doc-id: '+value+'\n---\nSaved source.\n')
                before=other.read_bytes();self.document(['Alex: new source.\n'*60]);r=self.run_import()
                self.assertNotEqual(r.returncode,0);self.assertEqual(self.notes(),[]);self.assertEqual(other.read_bytes(),before)

    def test_ambiguous_source_frontmatter_stops_before_export(self):
        other=self.vault/'system/transcripts/other.md'
        for header in ['gemini-doc-id: other\ngemini-doc-id: fixture-doc', '"gemini-doc-id": fixture-doc', 'gemini-doc-id: [fixture-doc]']:
            with self.subTest(header=header):
                other.write_text('---\n'+header+'\n---\nSaved source.\n')
                self.document(['Alex: new source.\n'*60]);r=self.run_import()
                self.assertNotEqual(r.returncode,0);self.assertEqual(self.notes(),[])
                self.assertIn('inventory',r.stderr)

    def test_symlink_inventory_is_not_followed(self):
        outside=self.root/'outside.md';outside.write_text('---\ngemini-doc-id: fixture-doc\n---\nOutside source.\n')
        (self.vault/'system/transcripts/link.md').symlink_to(outside)
        before=outside.read_bytes();self.document(['Alex: new source.\n'*60]);r=self.run_import()
        self.assertNotEqual(r.returncode,0);self.assertEqual(self.notes(),[]);self.assertEqual(outside.read_bytes(),before)

    def test_duplicate_source_id_records_require_review(self):
        for name in ['one','two']:
            (self.vault/'system/transcripts'/(name+'.md')).write_text('---\ngemini-doc-id: fixture-doc\n---\nSaved source.\n')
        self.document(['Alex: new source.\n'*60]);r=self.run_import()
        self.assertNotEqual(r.returncode,0);self.assertIn('inventory',r.stderr);self.assertEqual(self.notes(),[])

    def test_collision_body_mention_does_not_claim_identity(self):
        import datetime
        date='undated'
        intake=self.vault/'system/intake';intake.mkdir(exist_ok=True)
        other=intake/(date+'-fixture.md')
        other.write_text('---\ngemini-doc-id: other-document\n---\ngemini-doc-id: fixture-doc\n')
        before=other.read_bytes();self.document(['Alex: new source.\n'*60]);r=self.run_import()
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(len(self.notes()),2);self.assertEqual(other.read_bytes(),before)

    def test_wrong_document_id_never_publishes(self):
        self.document(['Alex: plausible text.\n'*60])
        data=json.loads(self.doc.read_text());data['documentId']='different-doc';self.doc.write_text(json.dumps(data))
        r=self.run_import();self.assertNotEqual(r.returncode,0);self.assertEqual(self.notes(),[])
        self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_failed_cli_with_valid_document_json_never_publishes(self):
        self.document(['Alex: plausible text.\n'*60]);self.env['DOC_EXIT']='8';r=self.run_import()
        self.assertNotEqual(r.returncode,0);self.assertEqual(self.notes(),[])
        self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_metadata_mismatch_failure_or_invalid_title_stops_before_full_fetch(self):
        for mode in ['wrong-id','bad-title','failed']:
            with self.subTest(mode=mode):
                self.env['META_MODE']=mode;self.document(['Alex: plausible text.\n'*60]);r=self.run_import()
                self.assertNotEqual(r.returncode,0);self.assertEqual(self.notes(),[])
                calls=[json.loads(x) for x in self.calls.read_text().splitlines()]
                docs=[c for c in calls if c['args'][:3]==['docs','documents','get']]
                self.assertFalse(any(json.loads(c['args'][c['args'].index('--params')+1]).get('includeTabsContent') for c in docs))
                self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_calendar_invitees_preserved_without_claiming_attendance(self):
        invitees=[{'displayName':'Alex "AJ" \\ Smith','email':'alex@example.test','responseStatus':'declined'},
                  {'email':'no-name@example.test','optional':True}]
        event={'summary':'Source fixture','start':{'dateTime':'2026-09-09T14:00:00Z'},'organizer':{'email':'organizer@example.test'},
               'attendees':invitees,'attachments':[{'title':'Notes by Gemini','fileId':'fixture-doc'}]}
        self.document(['Robin: actual speaker.\n'*60])
        r=self.enumeration(extra={'CALENDAR_MODE':'metadata','CALENDAR_EVENT':json.dumps(event)})
        self.assertEqual(r.returncode,0,r.stderr)
        note=self.notes()[0].read_text();header=note.split('---',2)[1]
        line=next(x for x in header.splitlines() if x.startswith('calendar-invitees: '))
        self.assertEqual(json.loads(line.split(': ',1)[1]),invitees)
        self.assertIn('attendees-from-source: []',header)
        self.assertIn('not evidence of attendance',note)

    def test_malformed_invitees_do_not_become_empty_or_invented_people(self):
        event={'summary':'Fixture','start':{'dateTime':'2026-09-09T14:00:00Z'},'attendees':'invalid',
               'attachments':[{'title':'Notes by Gemini','fileId':'fixture-doc'}]}
        self.document(['Robin: actual speaker.\n'*60])
        r=self.enumeration(extra={'CALENDAR_MODE':'metadata','CALENDAR_EVENT':json.dumps(event)})
        self.assertNotEqual(r.returncode,0);self.assertEqual(self.notes(),[])
        self.assertEqual(json.loads(self.checkpoint.read_text())['last_success_at'],'2026-09-01T00:00:00Z')

    def test_single_document_without_calendar_context_stays_undated(self):
        self.document(['Alex: no meeting date supplied.\n'*60]);r=self.run_import();self.assertEqual(r.returncode,0,r.stderr)
        note=self.notes()[0];self.assertTrue(note.name.startswith('undated-'))
        header=note.read_text().split('---',2)[1]
        for value in ['date: null','event-start: null','event-organizer: null']:self.assertIn(value,header)
        self.assertIn('meeting date unknown',note.read_text())

    def test_all_day_calendar_date_is_preserved(self):
        event={'summary':'All day fixture','start':{'date':'2026-09-01'},'attachments':[{'title':'Notes by Gemini','fileId':'fixture-doc'}]}
        self.document(['Alex: source.\n'*60]);r=self.enumeration(extra={'CALENDAR_MODE':'metadata','CALENDAR_EVENT':json.dumps(event)})
        self.assertEqual(r.returncode,0,r.stderr);self.assertTrue(self.notes()[0].name.startswith('2026-09-01-'))

    def test_invalid_calendar_date_does_not_fall_back_to_today(self):
        event={'summary':'Bad date fixture','start':{'date':'2026-02-30'},'attachments':[{'title':'Notes by Gemini','fileId':'fixture-doc'}]}
        self.document(['Alex: source.\n'*60]);r=self.enumeration(extra={'CALENDAR_MODE':'metadata','CALENDAR_EVENT':json.dumps(event)})
        self.assertNotEqual(r.returncode,0);self.assertEqual(self.notes(),[])
        self.assertEqual(json.loads(self.checkpoint.read_text())['last_success_at'],'2026-09-01T00:00:00Z')

    def test_offset_timestamp_uses_operator_timezone_date(self):
        event={'summary':'Offset fixture','start':{'dateTime':'2026-09-09T00:30:00.123+14:00'},'attachments':[{'title':'Notes by Gemini','fileId':'fixture-doc'}]}
        self.document(['Alex: source.\n'*60]);r=self.enumeration(extra={'TZ':'UTC','CALENDAR_MODE':'metadata','CALENDAR_EVENT':json.dumps(event)})
        self.assertEqual(r.returncode,0,r.stderr);self.assertTrue(self.notes()[0].name.startswith('2026-09-08-'))

    def test_unavailable_and_short_transcripts_require_review_without_success_advance(self):
        event={'summary':'Unresolved fixture','start':{'dateTime':'2026-09-09T14:00:00Z'},'attachments':[{'title':'Notes by Gemini','fileId':'fixture-doc'}]}
        for mode,result in [('short',1),('denied',5)]:
            with self.subTest(mode=mode):
                if mode=='short':self.document(['Brief actual transcript.\n'])
                else:self.doc.write_text('{"error":{"code":403}}')
                r=self.enumeration(extra={'CALENDAR_MODE':'metadata','CALENDAR_EVENT':json.dumps(event)})
                self.assertEqual(r.returncode,1,r.stderr);self.assertEqual(self.notes(),[])
                state=json.loads(self.checkpoint.read_text())
                self.assertEqual(state['last_success_at'],'2026-09-01T00:00:00Z')
                self.assertEqual(state['unresolved_sources'][0]['document_id'],'fixture-doc')
                self.assertEqual(state['unresolved_sources'][0]['result'],result)
                self.assertEqual(state['unresolved_sources'][0]['record'][0],'fixture-doc')

    def test_unresolved_source_retried_when_calendar_attachment_disappears(self):
        event={'summary':'Recovery fixture','start':{'dateTime':'2026-09-09T14:00:00Z'},'attachments':[{'title':'Notes by Gemini','fileId':'fixture-doc'}]}
        self.doc.write_text('{"error":{"code":403}}')
        r=self.enumeration(extra={'CALENDAR_MODE':'metadata','CALENDAR_EVENT':json.dumps(event)});self.assertEqual(r.returncode,1,r.stderr)
        r=self.enumeration();self.assertEqual(r.returncode,1,r.stderr)
        self.assertEqual(len(json.loads(self.checkpoint.read_text())['unresolved_sources']),1)
        self.document(['Alex: recovered source.\n'*60]);r=self.enumeration();self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(len(self.notes()),1);self.assertEqual(json.loads(self.checkpoint.read_text())['unresolved_sources'],[])

    def test_auth_failure_preserves_existing_unresolved_queue(self):
        record=['fixture-doc','Recovery fixture','2026-09-09T14:00:00Z','',[]]
        pending=[{'document_id':'fixture-doc','result':5,'record':record}]
        self.checkpoint.write_text(json.dumps({'account':'fixture@example.test','last_success_at':None,'unresolved_sources':pending,'coverage_start_at':'2026-09-01T00:00:00Z'}))
        r=self.enumeration(extra={'WRONG_IDENTITY':'1'});self.assertEqual(r.returncode,2)
        state=json.loads(self.checkpoint.read_text());self.assertEqual(state['unresolved_sources'],pending)
        self.assertIsNone(state['last_success_at']);self.assertEqual(state['coverage_start_at'],'2026-09-01T00:00:00Z')

    def test_first_failure_records_lookback_anchor_without_claiming_success(self):
        self.checkpoint.unlink()
        r=self.enumeration(extra={'WRONG_IDENTITY':'1'});self.assertEqual(r.returncode,2,r.stderr)
        state=json.loads(self.checkpoint.read_text());self.assertIsNone(state['last_success_at'])
        self.assertTrue(state['coverage_start_at'].endswith('Z'))
        state['coverage_start_at']='2026-09-01T00:00:00Z';self.checkpoint.write_text(json.dumps(state))
        r=self.enumeration();self.assertEqual(r.returncode,0,r.stderr)
        calls=[json.loads(x) for x in self.calls.read_text().splitlines()]
        page=next(c for c in calls if c['args'][:3]==['calendar','events','list'])
        params=json.loads(page['args'][page['args'].index('--params')+1])
        self.assertLessEqual(params['timeMin'],'2026-09-01T00:00:00Z')

    def test_invalid_recovery_date_fails_before_provider(self):
        state=json.loads(self.before);state['coverage_start_at']='2026-02-30T00:00:00Z';self.checkpoint.write_text(json.dumps(state))
        r=self.enumeration();self.assertEqual(r.returncode,2);self.assertFalse(self.calls.exists())

    def test_nested_transcript_and_date_display_text_preserved(self):
        self.document(['Alex: exact source.\n'*60]);data=json.loads(self.doc.read_text())
        transcript=data['tabs'].pop()
        transcript['documentTab']['body']['content'][0]['paragraph']['elements'].insert(0,{'dateElement':{'dateElementProperties':{'displayText':'Sep 2, 2026'}}})
        data['tabs'].append({'tabProperties':{'title':'Container'},'childTabs':[transcript]})
        self.doc.write_text(json.dumps(data));r=self.run_import();self.assertEqual(r.returncode,0,r.stderr)
        body=self.notes()[0].read_text().split('## Transcript\n\n',1)[1]
        self.assertEqual(body,'Sep 2, 2026'+'Alex: exact source.\n'*60)

    def test_unsupported_content_and_duplicate_transcript_tabs_fail_closed(self):
        for mode in ['image','duplicate-tab','missing-display']:
            with self.subTest(mode=mode):
                self.document(['Alex: exact source.\n'*60]);data=json.loads(self.doc.read_text());tab=data['tabs'][-1]
                if mode=='duplicate-tab':data['tabs'].append(tab)
                else:tab['documentTab']['body']['content'][0]['paragraph']['elements'].append({'inlineObjectElement':{'inlineObjectId':'image'}} if mode=='image' else {'dateElement':{'dateElementProperties':{}}})
                self.doc.write_text(json.dumps(data));r=self.run_import()
                self.assertNotEqual(r.returncode,0);self.assertEqual(self.notes(),[]);self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_vault_lock_blocks_both_accounts_before_provider_and_releases(self):
        lock = self.checkpoint.parent.parent/'import.lock'
        with lock.open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for account in ['fixture@example.test','second@example.test']:
                r=self.enumeration(account)
                self.assertEqual(r.returncode,2,r.stderr)
                self.assertIn('already running',r.stderr)
                self.assertFalse(self.calls.exists())
                self.assertEqual(self.checkpoint.read_bytes(),self.before)
            r=self.enumeration(args=['--status'])
            self.assertEqual(r.returncode,0,r.stderr)
        for account in ['fixture@example.test','second@example.test']:
            r=self.enumeration(account)
            self.assertEqual(r.returncode,0,r.stderr)

    def test_invalid_inherited_lock_cannot_skip_serialization(self):
        r=self.enumeration(extra={'WORKDESK_GEMINI_LOCK_FD':'99999'})
        self.assertEqual(r.returncode,2,r.stderr)
        self.assertFalse(self.calls.exists())
        self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_invalid_calendar_metadata_records_initial_failure_before_export(self):
        base={'summary':'Fixture','start':{'date':'2026-09-09'},'attachments':[{'title':'Notes by Gemini','fileId':'fixture-doc'}]}
        for field,value in [('summary',None),('summary',[]),('summary','  '),('start',{'date':'2026-09-09','dateTime':'2026-09-09T00:00:00Z'}),('organizer',[]),('attachments',{})]:
            with self.subTest(field=field,value=value):
                self.checkpoint.unlink(missing_ok=True)
                event=dict(base);event[field]=value
                r=self.enumeration(extra={'CALENDAR_MODE':'metadata','CALENDAR_EVENT':json.dumps(event)})
                self.assertEqual(r.returncode,2,r.stderr);self.assertEqual(self.notes(),[])
                state=json.loads(self.checkpoint.read_text())
                self.assertIsNone(state.get('last_success_at'))
                self.assertTrue(state['coverage_start_at'].endswith('Z'))
                self.assertEqual(state['last_failure_reason'],'calendar-metadata')
                calls=[json.loads(x) for x in self.calls.read_text().splitlines()]
                self.assertFalse(any(c['args'][:3]==['docs','documents','get'] for c in calls))

    def test_success_clears_failure_reason_after_metadata_recovery(self):
        state=json.loads(self.before);state['last_failure_reason']='calendar-metadata'
        self.checkpoint.write_text(json.dumps(state))
        r=self.enumeration();self.assertEqual(r.returncode,0,r.stderr)
        state=json.loads(self.checkpoint.read_text())
        self.assertIsNone(state['last_failure_at']);self.assertIsNone(state['last_failure_reason'])

    def test_absent_organizer_is_unknown_without_placeholder(self):
        self.document(['Alex: actual source.\n'*60])
        event={'summary':'Fixture','start':{'date':'2026-09-09'},'attachments':[{'title':'Notes by Gemini','fileId':'fixture-doc'}]}
        r=self.enumeration(extra={'CALENDAR_MODE':'metadata','CALENDAR_EVENT':json.dumps(event)})
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('event-organizer: null\n',self.notes()[0].read_text())

    def test_duplicate_or_malformed_pending_records_preserved_before_provider(self):
        record=['fixture-doc','Fixture','2026-09-09T00:00:00Z','',[]]
        for mode in ['duplicate','title','invitees']:
            with self.subTest(mode=mode):
                row=list(record)
                if mode=='title':row[1]=[]
                if mode=='invitees':row[4]='invalid'
                entries=[{'document_id':'fixture-doc','record':row,'result':5}]
                if mode=='duplicate':entries*=2
                data=json.dumps({'account':'fixture@example.test','unresolved_sources':entries})
                self.checkpoint.write_text(data)
                r=self.enumeration();self.assertEqual(r.returncode,2,r.stderr)
                self.assertFalse(self.calls.exists());self.assertEqual(self.checkpoint.read_text(),data)

    def test_reviewed_short_transcript_preserves_exact_body_and_checkpoint(self):
        text='Sep 3, 2026\nAlex: One word.\n'
        self.document([text]);digest=hashlib.sha256(text.encode()).hexdigest()
        r=self.run_import('--reviewed-short-sha256',digest);self.assertEqual(r.returncode,0,r.stderr)
        note=self.notes()[0].read_text()
        self.assertEqual(note.split('## Transcript\n\n',1)[1],text)
        self.assertIn('short-transcript-review: exact-content-reviewed',note)
        self.assertIn('reviewed-transcript-sha256: '+digest,note)
        self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_reviewed_checksum_mismatch_blocks_short_and_long_content(self):
        for text in ['Alex: short.\n','Alex: long content.\n'*100]:
            with self.subTest(size=len(text)):
                self.document([text]);r=self.run_import('--reviewed-short-sha256','0'*64)
                self.assertNotEqual(r.returncode,0);self.assertEqual(self.notes(),[])
                self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_short_review_cannot_authorize_enumeration_or_duplicate_flags(self):
        for args in [('--reviewed-short-sha256','0'*64),('--doc-id','fixture-doc','--force','--reviewed-short-sha256','bad'),('--doc-id','fixture-doc','--force','--reviewed-short-sha256','0'*64,'--reviewed-short-sha256','0'*64)]:
            r=self.enumeration(args=args);self.assertEqual(r.returncode,2,r.stderr)
            self.assertFalse(self.calls.exists());self.assertEqual(self.checkpoint.read_bytes(),self.before)

    def test_missing_transcript_remains_gap_then_recovers(self):
        event={'summary':'Missing transcript fixture','start':{'date':'2026-09-09'},'attachments':[{'title':'Notes by Gemini','fileId':'fixture-doc'}]}
        self.doc.write_text(json.dumps({'documentId':'fixture-doc','tabs':[{'tabProperties':{'title':'Full notes'},'documentTab':{'body':{'content':[{'paragraph':{'elements':[{'textRun':{'content':'Summary must never become transcript.\n'*50}}]}}]}}}]}))
        r=self.enumeration(extra={'CALENDAR_MODE':'metadata','CALENDAR_EVENT':json.dumps(event)})
        self.assertEqual(r.returncode,1,r.stderr);self.assertEqual(self.notes(),[])
        state=json.loads(self.checkpoint.read_text());self.assertEqual(state['unresolved_sources'][0]['result'],6)
        self.assertEqual(state['last_success_at'],'2026-09-01T00:00:00Z')
        r=self.enumeration(args=['--status']);self.assertIn('HEALTH: INCOMPLETE',r.stdout)
        self.assertIn('missing Transcript tab',r.stdout);self.assertNotIn('HEALTH: ok',r.stdout)
        self.document(['Alex: Transcript now available.\n'*60])
        r=self.enumeration();self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(len(self.notes()),1);self.assertEqual(json.loads(self.checkpoint.read_text())['unresolved_sources'],[])

    def test_status_does_not_hide_failure_behind_recent_success(self):
        state={'account':'fixture@example.test','last_success_at':'2099-01-01T00:00:00Z','consecutive_failures':1}
        self.checkpoint.write_text(json.dumps(state));r=self.enumeration(args=['--status'])
        self.assertEqual(r.returncode,0,r.stderr);self.assertIn('HEALTH: INCOMPLETE',r.stdout)
        self.assertNotIn('HEALTH: ok',r.stdout);self.assertFalse(self.calls.exists())

if __name__=='__main__':unittest.main(verbosity=2)
