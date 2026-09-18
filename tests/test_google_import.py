"""Real importer + account component, isolated vault and synthetic provider."""
import datetime as dt
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
ACCOUNTS = ('one@example.com', 'two@example.com')

FAKE = r'''#!/usr/bin/env python3
import json,os,sys,time,datetime
from pathlib import Path
args=sys.argv[1:]; mode=os.environ.get('FAKE_MODE','empty')
account=Path(os.environ['GOOGLE_WORKSPACE_CLI_CONFIG_DIR']).name
with open(os.environ['CALLS'],'a') as f:f.write(json.dumps({'account':account,'args':args,'at':datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})+'\n')
assert 'GOOGLE_WORKSPACE_CLI_TOKEN' not in os.environ
if args==['--version']:print('gws 0.22.5')
elif args[:3]==['drive','about','get']:
 print(json.dumps({'user':{'emailAddress':'wrong@example.com' if mode=='wrong' else account}}))
elif args[:3]==['drive','files','list']:
 params=json.loads(args[args.index('--params')+1]);second='pageToken' in params
 if mode=='page-fail' and second:sys.exit(7)
 if mode=='slow-empty':time.sleep(2)
 if mode in ['empty','slow-empty']:print('{"files":[]}')
 else:
  data={'files':[{'id':'id-second' if second else 'id-first','name':'Fixture - 2026/09/09 09:00 CDT - Transcript','createdTime':'2026-09-09T14:00:00Z'}]}
  if mode in ['pages','page-fail'] and not second:data['nextPageToken']='next'
  print(json.dumps(data))
elif args[:3]==['drive','files','get']:
 print('{"id":"id-first","name":"Fixture - 2026/09/09 09:00 CDT - Transcript","createdTime":"2026-09-09T14:00:00Z"}')
elif args[:3]==['drive','files','export']:
 if mode=='export-fail':sys.exit(8)
 output=Path(args[args.index('--output')+1]);assert not output.is_absolute()
 output.write_bytes(b'Unexpected layout without transcript section' if mode=='no-body' else b'Attendees\r\nAlex\r\nTranscript\r\nAlex: Preserve this exact source statement.\r\n')
 if mode=='long-body':output.write_text('Attendees\nAlex\nTranscript\n'+'Alex: Preserve this exact source statement.\n'*3000)
 if mode=='headerless':output.write_bytes(b'Alex: Preserve the original turn.\nRobin: Understood.\n')
 if mode=='headerless-attendees':output.write_bytes(b'Attendees\nAlex, Robin\n00:35:00\nAlex: Preserve dialogue.\n')
 if mode=='contaminated-attendees':output.write_bytes(b'Attendees\nAlex\n00:35:00\nRobin: Not an attendee name.\nTranscript\nAlex: Preserve dialogue.\n')
 if mode=='oversized-attendees':output.write_text('Attendees\n'+''.join('Person '+str(i)+'\n' for i in range(33))+'Transcript\nAlex: Preserve dialogue.\n')
else:sys.exit(90)
'''


class GoogleImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.vault = self.root/'vault'
        scripts = self.vault/'config/scripts'
        (scripts/'lib').mkdir(parents=True)
        (self.vault/'system/transcripts').mkdir(parents=True)
        for rel in ['pull-google-transcripts.sh', 'lib/gws_account.py']:
            shutil.copy2(ROOT/'config/scripts'/rel, scripts/rel)
        self.script = scripts/'pull-google-transcripts.sh'
        self.bin = self.root/'bin'; self.bin.mkdir()
        self.gws = self.bin/'gws'; self.gws.write_text(FAKE); self.gws.chmod(0o755)
        routes = {}
        for account in ACCOUNTS:
            path=self.root/account;path.mkdir()
            routes[account]={'mode':'config-dir','config_dir':str(path)}
        self.calls=self.root/'calls';self.state=self.root/'state'
        self.env=dict(os.environ, PATH=str(self.bin)+':'+str(Path(sys.executable).parent)+':'+os.environ['PATH'],
                      WORKDESK_PYTHON=sys.executable,WORKDESK_GWS_BIN=str(self.gws),
                      WORKDESK_STATE_HOME=str(self.state),WORKDESK_GWS_ACCOUNTS=json.dumps(routes),
                      GOOGLE_WORKSPACE_CLI_TOKEN='synthetic-conflicting-token',CALLS=str(self.calls))
        self.env.pop('WORKDESK_GWS_ACCOUNT',None)

    def run_import(self, account=ACCOUNTS[0], args=(), mode='empty'):
        cmd=['bash',str(self.script)]
        if account is not None:cmd+=['--account',account]
        return subprocess.run(cmd+list(args),env=dict(self.env,FAKE_MODE=mode),capture_output=True,text=True,timeout=30)

    def checkpoint(self, account=ACCOUNTS[0]):
        vault=hashlib.sha256(str(self.vault).encode()).hexdigest()[:16]
        principal=hashlib.sha256(account.encode()).hexdigest()[:32]
        return self.state/vault/'google-transcripts'/principal/'pull-google.json'

    def seed(self, account, days):
        when=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
        path=self.checkpoint(account);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'account':account,'last_success_at':when,'consecutive_failures':0}))
        return path,when

    def events(self):
        return [json.loads(x) for x in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def notes(self):return list((self.vault/'system/intake').glob('*.md'))

    def test_explicit_account_required_before_access(self):
        self.assertEqual(self.run_import(None).returncode,2)
        self.assertEqual(self.events(),[])
        self.assertFalse(self.state.exists())

    def test_wrong_identity_blocks_listing_and_records_only_selected_failure(self):
        result=self.run_import(mode='wrong');self.assertEqual(result.returncode,2,result.stderr)
        self.assertFalse(any(e['args'][:2]==['drive','files'] for e in self.events()))
        state=json.loads(self.checkpoint().read_text())
        self.assertEqual(state['account'],ACCOUNTS[0]);self.assertIsNone(state['last_success_at'])
        self.assertFalse(self.checkpoint(ACCOUNTS[1]).exists())

    def test_each_account_uses_its_own_lookback_and_preserves_legacy(self):
        path,old=self.seed(ACCOUNTS[0],10)
        legacy=self.vault/'config/state/pull-google.json';legacy.parent.mkdir()
        legacy.write_text('{"last_success_at":"2001-01-01T00:00:00Z"}')
        before=legacy.read_bytes()
        for account in ACCOUNTS:
            result=self.run_import(account);self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(self.checkpoint(account).read_text())['account'],account)
        lists=[e for e in self.events() if e['args'][:3]==['drive','files','list']]
        cuts=[json.loads(e['args'][e['args'].index('--params')+1])['q'].split('createdTime > "')[1].split('"')[0] for e in lists]
        self.assertLessEqual(cuts[0],old);self.assertGreater(cuts[1],old)
        self.assertEqual(legacy.read_bytes(),before)

    def test_misattributed_checkpoint_stops_before_access(self):
        path,_=self.seed(ACCOUNTS[0],2)
        data=json.loads(path.read_text());data['account']=ACCOUNTS[1];path.write_text(json.dumps(data));before=path.read_bytes()
        self.assertEqual(self.run_import().returncode,2)
        self.assertEqual(self.events(),[]);self.assertEqual(path.read_bytes(),before)

    def test_all_pages_export_relative_and_replay_preserves_notes(self):
        result=self.run_import(mode='pages');self.assertEqual(result.returncode,0,result.stderr)
        before={p.name:p.read_bytes() for p in self.notes()};self.assertEqual(len(before),2)
        self.assertTrue(all(b'Alex: Preserve this exact source statement.' in x and b'\r' not in x for x in before.values()))
        result=self.run_import(mode='pages');self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.notes()})
        self.assertEqual(sum(e['args'][:3]==['drive','files','export'] for e in self.events()),2)

    def test_failed_later_page_preserves_watermark_and_publishes_nothing(self):
        path,old=self.seed(ACCOUNTS[0],3)
        result=self.run_import(mode='page-fail');self.assertEqual(result.returncode,2,result.stderr)
        self.assertEqual(json.loads(path.read_text())['last_success_at'],old)
        self.assertEqual(self.notes(),[])
        self.assertFalse(any(e['args'][:3]==['drive','files','export'] for e in self.events()))

    def test_dry_run_never_changes_checkpoint_even_on_auth_failure(self):
        path,_=self.seed(ACCOUNTS[0],3);before=path.read_bytes()
        for mode in ['pages','wrong']:
            self.run_import(args=['--dry-run'],mode=mode)
            self.assertEqual(path.read_bytes(),before);self.assertEqual(self.notes(),[])

    def test_single_file_failure_and_collision_never_advance_enumeration(self):
        path,_=self.seed(ACCOUNTS[0],3);before=path.read_bytes()
        args=['--file-id','id-first','--force']
        self.assertEqual(self.run_import(args=args,mode='export-fail').returncode,1)
        self.assertEqual(path.read_bytes(),before)
        good=self.run_import(args=args,mode='one');self.assertEqual(good.returncode,0,good.stderr)
        self.assertEqual(path.read_bytes(),before)
        note=self.notes()[0];content=note.read_bytes()
        self.assertEqual(self.run_import(args=args,mode='one').returncode,1)
        self.assertEqual(note.read_bytes(),content);self.assertEqual(path.read_bytes(),before)

    def test_missing_and_invalid_arguments_do_not_access_google(self):
        for args in [['--days'],['--days','0'],['--days','words'],['--file-id'],['--file-id','id-first']]:
            self.assertEqual(self.run_import(args=args).returncode,2,args)
        self.assertEqual(self.events(),[])

    def test_success_watermark_precedes_long_listing_completion(self):
        result=self.run_import(mode='slow-empty');self.assertEqual(result.returncode,0,result.stderr)
        state=json.loads(self.checkpoint().read_text())
        listing=next(e for e in self.events() if e['args'][:3]==['drive','files','list'])
        self.assertLessEqual(state['last_success_at'],listing['at'])
        self.assertLess(state['last_success_at'],state['last_run_at'])

    def test_unrecognized_export_does_not_publish_or_advance_watermark(self):
        path,old=self.seed(ACCOUNTS[0],3)
        result=self.run_import(mode='no-body');self.assertEqual(result.returncode,1,result.stderr)
        self.assertEqual(self.notes(),[])
        self.assertEqual(json.loads(path.read_text())['last_success_at'],old)

    def test_long_transcript_is_preserved_within_bounded_runtime(self):
        result=self.run_import(mode='long-body');self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(self.notes()),1)
        body=self.notes()[0].read_text().split('## Transcript\n\n',1)[1]
        self.assertEqual(body,'Alex: Preserve this exact source statement.\n'*3000)

    def test_headerless_recovery_requires_reviewed_hash_and_single_file(self):
        raw=b'Alex: Preserve the original turn.\nRobin: Understood.\n'
        digest=hashlib.sha256(raw).hexdigest()
        self.assertEqual(self.run_import(mode='headerless').returncode,1)
        self.assertEqual(self.notes(),[])
        args=['--file-id','id-first','--force','--reviewed-headerless-sha256']
        self.assertEqual(self.run_import(args=args+['0'*64],mode='headerless').returncode,1)
        self.assertEqual(self.notes(),[])
        result=self.run_import(args=args+[digest],mode='headerless');self.assertEqual(result.returncode,0,result.stderr)
        text=self.notes()[0].read_text()
        self.assertEqual(text.split('## Transcript\n\n',1)[1],raw.decode())
        self.assertIn(digest,text)
        self.assertIn('attendees-from-source:\n  []',text)
        self.assertIsNone(json.loads(self.checkpoint().read_text())['last_success_at'])

    def test_headerless_attendees_never_consume_dialogue(self):
        raw=b'Attendees\nAlex, Robin\n00:35:00\nAlex: Preserve dialogue.\n'
        args=['--file-id','id-first','--force','--reviewed-headerless-sha256',hashlib.sha256(raw).hexdigest()]
        result=self.run_import(args=args,mode='headerless-attendees')
        self.assertEqual(result.returncode,0,result.stderr)
        text=self.notes()[0].read_text()
        self.assertEqual(text.split('## Transcript\n\n',1)[1],raw.decode())
        header=text.split('\n---\n',1)[0]
        self.assertIn('attendee-extraction-status: not-extracted-headerless',header)
        self.assertIn('attendees-from-source:\n  []',header)
        self.assertNotIn('Preserve dialogue',header)

    def test_malformed_or_oversized_attendee_header_is_unknown(self):
        for mode in ['contaminated-attendees','oversized-attendees']:
            with self.subTest(mode=mode):
                result=self.run_import(mode=mode)
                self.assertEqual(result.returncode,0,result.stderr)
                text=self.notes()[0].read_text();header=text.split('\n---\n',1)[0]
                self.assertIn('attendees-from-source:\n  []',header)
                self.assertIn('attendee-extraction-status: unverified',header)
                self.assertEqual(text.split('## Transcript\n\n',1)[1],'Alex: Preserve dialogue.\n')
                # Separate provider fixtures must each exercise a fresh publication.
                self.notes()[0].unlink()

    def test_valid_attendee_header_retains_names_without_attendance_claim(self):
        result=self.run_import(mode='one');self.assertEqual(result.returncode,0,result.stderr)
        text=self.notes()[0].read_text();header=text.split('\n---\n',1)[0]
        self.assertIn('attendees-from-source:\n  - "Alex"',header)
        self.assertIn('attendee-extraction-status: header-list',header)

    def test_headerless_option_rejected_without_single_file_scope(self):
        self.assertEqual(self.run_import(args=['--reviewed-headerless-sha256','0'*64]).returncode,2)
        self.assertEqual(self.events(),[])


if __name__=='__main__':unittest.main()
