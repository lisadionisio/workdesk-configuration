import os,json,tempfile,shutil,subprocess,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
class Resolver(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.lib=self.root/'config/scripts/lib';self.lib.mkdir(parents=True)
  for n in ['resolve-secret.sh','operator-config.sh']:shutil.copy2(HERE.parent/'config/scripts/lib'/n,self.lib/n)
  (self.root/'config/operator-profile.md').write_text('---\nname: Synthetic Operator\nemail: operator@example.test\ninfisical-project-id: synthetic-project\n---\n')
  self.bin=self.root/'bin';self.bin.mkdir();self.trace=self.root/'calls'
  self.env={'PATH':str(self.bin)+':/usr/bin:/bin','HOME':str(self.root),'TRACE':str(self.trace)}
 def provider(self,output,code):
  p=self.bin/'infisical';p.write_text('#!/bin/bash\nprintf \"called\\n\" >> \"$TRACE\"\nprintf \"%s\" '+repr(output)+'\nexit '+str(code)+'\n');p.chmod(0o700)
 def resolve(self):return subprocess.run(['/bin/bash','-c','source \"$1\"; wd_resolve_secret PERSONAL_GRANOLA_API_KEY','test',str(self.lib/'resolve-secret.sh')],env=self.env,capture_output=True,text=True)
 def test_environment_value_wins_without_provider(self):
  self.provider('unused',1);self.env['PERSONAL_GRANOLA_API_KEY']='synthetic-env';r=self.resolve();self.assertEqual((r.returncode,r.stdout),(0,'synthetic-env'));self.assertFalse(self.trace.exists())
 def test_successful_provider(self):
  self.provider('synthetic-provider',0);r=self.resolve();self.assertEqual((r.returncode,r.stdout),(0,'synthetic-provider'));self.assertTrue(self.trace.exists())
 def test_successful_empty_provider_is_not_a_credential(self):
  self.provider('',0);r=self.resolve();self.assertNotEqual(r.returncode,0);self.assertEqual(r.stdout,'')
 def test_empty_failed_provider(self):
  self.provider('',1);r=self.resolve();self.assertNotEqual(r.returncode,0);self.assertEqual(r.stdout,'')
 def test_failed_provider_stdout_is_not_a_credential(self):
  self.provider('grn_synthetic_partial_failure',1);r=self.resolve();self.assertNotEqual(r.returncode,0);self.assertEqual(r.stdout,'')
if __name__=='__main__':unittest.main(verbosity=2)
