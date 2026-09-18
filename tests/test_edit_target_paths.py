import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('targets',ROOT/'config/scripts/edit-target-paths.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class TargetTests(unittest.TestCase):
    def test_patch_content_is_not_a_destination(self):
        patch='*** Begin Patch\n*** Update File: config/note.md\n@@\n+Mention restricted/example.md in prose.\n+*** Delete File: other.md\n*** End Patch'
        self.assertEqual(module.patch_paths(patch),['config/note.md'])

    def test_all_added_updated_deleted_and_move_paths_are_returned(self):
        patch='*** Begin Patch\n*** Add File: new.md\n+source\n*** Update File: old.md\n*** Move to: moved.md\n@@\n-old\n+new\n*** Delete File: removed.md\n*** End Patch'
        self.assertEqual(module.patch_paths(patch),['new.md','old.md','moved.md','removed.md'])

    def test_direct_edit_fields_not_body_or_command(self):
        for tool in ['Edit','Write','MultiEdit','NotebookEdit']:
            result=module.edit_targets({'tool_name':tool,'cwd':'/tmp','tool_input':{'file_path':'target.md','command':'different.md','content':'restricted/example.md'}})
            self.assertEqual([r['path'] for r in result],['target.md'])

    def test_symlink_and_parent_components_resolve(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'destination').mkdir();(root/'alias').symlink_to(root/'destination')
            result=module.edit_targets({'tool_name':'Write','cwd':str(root),'tool_input':{'path':'alias/../alias/file.md'}})
            self.assertEqual(result[0]['resolved'],str((root/'destination/file.md').resolve()))

    def test_invalid_payloads_and_ambiguous_destinations_fail(self):
        for payload in [None,{}, {'tool_name':'Write','cwd':'.','tool_input':{'path':'a'}},
                        {'tool_name':'Write','cwd':'/tmp','tool_input':{'path':'a','file_path':'b'}},
                        {'tool_name':'Write','cwd':'/tmp','tool_input':{'path':None}},
                        {'tool_name':'Bash','cwd':'/tmp','tool_input':{'command':'read-only'}},
                        {'tool_name':'Edit','cwd':'/tmp','tool_input':{'command':'a'}}]:
            with self.subTest(payload=payload),self.assertRaises(ValueError):module.edit_targets(payload)

    def test_malformed_patch_cannot_hide_destinations(self):
        for patch in ['', '*** Begin Patch\n*** End Patch',
                      '*** Begin Patch\n*** Move to: a\n*** End Patch',
                      '*** Begin Patch\n*** Add File: \n+x\n*** End Patch',
                      '*** Begin Patch\n*** Add File: a\n*** Unknown: b\n*** End Patch',
                      '*** Begin Patch\n*** Delete File: a\n+unexpected\n*** End Patch']:
            with self.subTest(patch=patch),self.assertRaises(ValueError):module.patch_paths(patch)


if __name__=='__main__':unittest.main()
