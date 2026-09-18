from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseArchiveTests(unittest.TestCase):
    def publish_fixture(self, root):
        for name in ('scripts/release.sh','tests/genericity-check.sh','tests/genericity-allowlist.txt'):
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,p)
        (root/'config').mkdir();(root/'config/VERSION').write_text('9.8.7\n')
        (root/'config/note.md').write_text('Synthetic public configuration.\n')
        (root/'.gitignore').write_text('dist/\n')
        for args in [('init','-q'),('add','.'),('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture')]:
            subprocess.run(['git',*args],cwd=root,check=True,capture_output=True)
        return subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()

    def fake_gh(self, root):
        tools=root/'fake-tools';tools.mkdir();log=root/'gh-calls.jsonl'
        stub=tools/'gh';stub.write_text('#!/usr/bin/env python3\nimport json,os,sys\nwith open(os.environ["TEST_GH_LOG"],"a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\nsys.exit(1 if sys.argv[1:3]==["release","view"] else 0)\n');stub.chmod(0o755)
        return dict(os.environ,PATH=str(tools)+os.pathsep+os.environ['PATH'],TEST_GH_LOG=str(log)),log

    def test_publish_built_uses_exact_bytes_and_pins_reviewed_commit(self):
        with tempfile.TemporaryDirectory(prefix='workdesk-publish-test-') as folder:
            parent=Path(folder);root=parent/'repo';root.mkdir();commit=self.publish_fixture(root)
            env,log=self.fake_gh(parent)
            subprocess.run(['bash','scripts/release.sh','--dry-run'],cwd=root,check=True,capture_output=True)
            archive=root/'dist/workdesk-os-9.8.7.tar.gz';before=archive.read_bytes();mtime=archive.stat().st_mtime_ns
            run=subprocess.run(['bash','scripts/release.sh','--publish-built'],cwd=root,env=env,text=True,capture_output=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertEqual(archive.read_bytes(),before);self.assertEqual(archive.stat().st_mtime_ns,mtime)
            calls=[json.loads(line) for line in log.read_text().splitlines()];create=next(c for c in calls if c[:2]==['release','create'])
            self.assertEqual(create[create.index('--target')+1],commit)

    def test_publish_built_rejects_changed_archive_or_source(self):
        for mutation in ('archive','new-commit','dirty-source','sidecar'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix='workdesk-publish-reject-') as folder:
                parent=Path(folder);root=parent/'repo';root.mkdir();self.publish_fixture(root);env,log=self.fake_gh(parent)
                subprocess.run(['bash','scripts/release.sh','--dry-run'],cwd=root,check=True,capture_output=True)
                archive=root/'dist/workdesk-os-9.8.7.tar.gz'
                if mutation=='archive':archive.write_bytes(archive.read_bytes()+b'changed')
                elif mutation=='sidecar':Path(str(archive)+'.sha256').write_text('wrong checksum\n')
                else:
                    (root/'config/note.md').write_text('Changed source.\n')
                    if mutation=='new-commit':subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qam','changed'],cwd=root,check=True,capture_output=True)
                run=subprocess.run(['bash','scripts/release.sh','--publish-built'],cwd=root,env=env,text=True,capture_output=True)
                self.assertNotEqual(run.returncode,0)
                calls=[json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
                self.assertFalse(any(c[:2]==['release','create'] for c in calls))

    def test_build_excludes_host_metadata_and_private_state_preserving_payload(self):
        with tempfile.TemporaryDirectory(prefix='workdesk-package-test-') as folder:
            root = Path(folder)
            for name in ('scripts/release.sh', 'tests/genericity-check.sh', 'tests/genericity-allowlist.txt'):
                target = root/name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT/name, target)
            config = root/'config'
            (config/'scripts').mkdir(parents=True)
            (config/'VERSION').write_text('9.8.7\n')
            payload = config/'scripts/check.sh'
            payload.write_text('#!/bin/sh\nprintf "ok\\n"\n')
            payload.chmod(0o755)
            (config/'scripts/linked.sh').symlink_to('check.sh')
            (config/'note.md').write_text('Synthetic distributable note.\n')
            if sys.platform == 'darwin':
                subprocess.run(['/usr/bin/xattr', '-w', 'com.workdesk.release-test',
                                'synthetic host metadata', str(config/'note.md')], check=True)
            for name in ('._note.md', '.DS_Store', 'operator-policy.md', 'cache.pyc',
                         'defaults/old.md', 'state/runtime.json', 'snapshots/prior.md', '__pycache__/a.pyc'):
                target = config/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('Synthetic excluded data.\n')
            process = subprocess.run(['bash', str(root/'scripts/release.sh'), '--dry-run'],
                                     capture_output=True, text=True, timeout=60)
            self.assertEqual(process.returncode, 0, process.stdout+process.stderr)
            archive = root/'dist/workdesk-os-9.8.7.tar.gz'
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                             Path(str(archive)+'.sha256').read_text().split()[0])
            with tarfile.open(archive) as opened:
                members = {m.name.removeprefix('./'): m for m in opened.getmembers()}
                self.assertFalse(any(part.startswith('._') for name in members for part in Path(name).parts))
                regular = {name for name,m in members.items() if m.isfile()}
                self.assertEqual(regular, {'manifest.json', 'workdesk/VERSION',
                                          'workdesk/note.md', 'workdesk/scripts/check.sh'})
                self.assertEqual(opened.extractfile(members['workdesk/scripts/check.sh']).read(), payload.read_bytes())
                self.assertEqual(members['workdesk/scripts/check.sh'].mode & 0o777, 0o755)
                link = members['workdesk/scripts/linked.sh']
                self.assertTrue(link.issym())
                self.assertEqual(link.linkname, 'check.sh')
                self.assertEqual(json.load(opened.extractfile(members['manifest.json'])),
                                 {'version': '9.8.7', 'migrations': []})


if __name__ == '__main__':
    unittest.main()
