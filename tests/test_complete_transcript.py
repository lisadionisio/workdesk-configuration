import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PRODUCT = Path(os.environ.get('WORKDESK_PRODUCT', Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location('completion', PRODUCT/'config/scripts/complete-transcript.py')
completion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(completion)

class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ['config/scripts', 'system/intake', 'system/transcripts', 'system/session-log', 'atlas/meetings']:
            (self.root/directory).mkdir(parents=True)
        for name in ['check-wikilinks.sh', 'check-wikilinks.py']:
            shutil.copyfile(PRODUCT/'config/scripts'/name, self.root/'config/scripts'/name)
        self.source = self.root/'system/intake/S9.md'
        self.raw = b'---\nsource-id: S9\nprocessed: false\n---\n# Source\nA verified statement.\n'
        self.source.write_bytes(self.raw)
        self.output = self.root/'atlas/meetings/M9.md'
        self.output.write_text('# Meeting\nSource: [[S9]]\n')
        self.receipts = self.root/'system/session-log'

    def run_completion(self):
        return completion.complete(self.root, 'system/intake/S9.md', ['atlas/meetings/M9.md'], self.receipts)

    def test_real_checker_success_orders_transition_and_preserves_body(self):
        receipt = self.run_completion()
        data = json.loads(receipt.read_text())
        self.assertTrue(data['complete'])
        self.assertLess(data['events'].index('pre_archive_verified'), data['events'].index('archive_linked_source_retained'))
        self.assertLess(data['events'].index('post_archive_verified'), data['events'].index('metadata_finalized'))
        destination = self.root/'system/transcripts/S9.md'
        self.assertFalse(self.source.exists())
        self.assertIn('processed: true', destination.read_text())
        self.assertTrue(destination.read_bytes().endswith(b'# Source\nA verified statement.\n'))
        self.assertEqual((receipt.parent/'source-before.snapshot').read_bytes(), self.raw)

    def test_precheck_failure_keeps_intake_unmodified(self):
        self.output.write_text('Broken [[missing-target]]\n')
        with self.assertRaises(subprocess.CalledProcessError): self.run_completion()
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertFalse((self.root/'system/transcripts/S9.md').exists())

    def test_quoted_meeting_properties_complete_and_revalidate(self):
        self.output.write_text('---\ntype: meeting\ntranscript: "[[S9]]"\nattendees: ["Alex"]\n---\n# Meeting\nSource: [[S9]]\n')
        receipt = self.run_completion()
        self.assertTrue(completion.verify_receipt(self.root, receipt)['complete'])

    def test_nested_transcript_property_blocks_archive(self):
        self.output.write_text('---\ntype: meeting\ntranscript: [[S9]]\nattendees: [Alex]\n---\n# Meeting\nSource: [[S9]]\n')
        with self.assertRaisesRegex(ValueError, 'quoted wikilink'):
            self.run_completion()
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertFalse((self.root/'system/transcripts/S9.md').exists())
        record = json.loads(next(self.receipts.glob('*/receipt.json')).read_text())
        self.assertFalse(record['complete'])

    def test_invalid_attendee_type_blocks_archive(self):
        self.output.write_text('---\ntranscript: "[[S9]]"\nattendees: Alex\n---\n# Meeting\nSource: [[S9]]\n')
        with self.assertRaisesRegex(ValueError, 'attendees'):
            self.run_completion()
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_duplicate_output_property_blocks_archive(self):
        self.output.write_text('---\ntranscript: "[[S9]]"\ntranscript: "[[S9]]"\n---\n# Meeting\nSource: [[S9]]\n')
        with self.assertRaisesRegex(ValueError, 'unique string keys'):
            self.run_completion()
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_readonly_output_verification_cli(self):
        self.output.write_text('---\ntranscript: "[[S9]]"\nattendees: [Alex]\n---\n# Meeting\nSource: [[S9]]\n')
        before = {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        command = [sys.executable, str(PRODUCT/'config/scripts/complete-transcript.py'), '--vault', str(self.root), '--verify-outputs', '--output', 'atlas/meetings/M9.md']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.output.write_text(self.output.read_text().replace('"[[S9]]"', '[[S9]]'))
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_archive_collision_preserves_both_files(self):
        target = self.root/'system/transcripts/S9.md'
        target.write_text('Other source\n')
        with self.assertRaises(ValueError): self.run_completion()
        self.assertEqual(target.read_text(), 'Other source\n')
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_extraction_gate_checks_source_and_preserves_all_files(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        command = [sys.executable, str(PRODUCT/'config/scripts/complete-transcript.py'), '--vault', str(self.root), '--verify-extraction', '--source', 'system/intake/S9.md', '--output', 'atlas/meetings/M9.md']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.source.write_bytes(self.raw + b'Broken source reference: [[missing-source-target]]\n')
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing-source-target', result.stdout)
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_extraction_gate_rejects_missing_or_symlink_source(self):
        for name in ('system/intake/missing.md', 'atlas/meetings/M9.md'):
            with self.subTest(source=name), self.assertRaises(ValueError):
                completion.verify_extraction(self.root, ['atlas/meetings/M9.md'], name)
        alias = self.root/'system/intake/alias.md'
        alias.symlink_to(self.source)
        with self.assertRaises(ValueError):
            completion.verify_extraction(self.root, ['atlas/meetings/M9.md'], 'system/intake/alias.md')
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_extraction_gate_rejects_bad_output_even_with_valid_source(self):
        self.output.write_text('---\ntranscript: [[S9]]\n---\nSource: [[S9]]\n')
        with self.assertRaisesRegex(ValueError, 'quoted wikilink'):
            completion.verify_extraction(self.root, ['atlas/meetings/M9.md'], 'system/intake/S9.md')
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_extraction_gate_does_not_accept_completion_arguments(self):
        command = [sys.executable, str(PRODUCT/'config/scripts/complete-transcript.py'), '--vault', str(self.root), '--verify-extraction', '--source', 'system/intake/S9.md', '--output', 'atlas/meetings/M9.md', '--receipts', str(self.receipts)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertEqual(list(self.receipts.iterdir()), [])

    def test_postcheck_failure_never_marks_complete(self):
        actual = completion.verify
        calls = []
        def verify(root, outputs, source):
            calls.append(source)
            if len(calls) == 2: raise RuntimeError('Synthetic postcheck failure')
            actual(root, outputs, source)
        with patch.object(completion, 'verify', side_effect=verify):
            with self.assertRaises(RuntimeError): self.run_completion()
        self.assertEqual((self.root/'system/transcripts/S9.md').read_bytes(), self.raw)
        receipt = next(self.receipts.glob('*/receipt.json'))
        self.assertFalse(json.loads(receipt.read_text())['complete'])

    def test_output_change_during_verification_stops_before_archive(self):
        def mutate(*args): self.output.write_text('Another writer\n')
        with patch.object(completion, 'verify', side_effect=mutate):
            with self.assertRaises(ValueError): self.run_completion()
        self.assertTrue(self.source.exists())
        self.assertFalse((self.root/'system/transcripts/S9.md').exists())

    def test_duplicate_metadata_requires_reconciliation(self):
        self.source.write_text('---\nprocessed: false\nprocessed: true\n---\nBody\n')
        with self.assertRaises(ValueError): self.run_completion()

    def test_interruption_after_archive_retains_uncompleted_source_and_journal(self):
        actual = completion.verify
        calls = []
        def interrupt(root, outputs, source):
            calls.append(source)
            if len(calls) == 2: raise SystemExit('Simulated process interruption')
            actual(root, outputs, source)
        with patch.object(completion, 'verify', side_effect=interrupt):
            with self.assertRaises(SystemExit): self.run_completion()
        self.assertEqual((self.root/'system/transcripts/S9.md').read_bytes(), self.raw)
        data = json.loads(next(self.receipts.glob('*/receipt.json')).read_text())
        self.assertFalse(data['complete'])
        self.assertEqual(data['events'][-1], 'archived_uncompleted')

    def test_failure_after_metadata_still_has_incomplete_receipt(self):
        actual = completion.verify
        calls = []
        def fail(root, outputs, source):
            calls.append(source)
            if len(calls) == 3: raise RuntimeError('Final verification failed')
            actual(root, outputs, source)
        with patch.object(completion, 'verify', side_effect=fail):
            with self.assertRaises(RuntimeError): self.run_completion()
        data = json.loads(next(self.receipts.glob('*/receipt.json')).read_text())
        self.assertFalse(data['complete'])
        self.assertEqual(data['events'][-1], 'incomplete_reconciliation_required')

    def test_symlink_output_is_rejected_without_mutation(self):
        alias = self.output.parent/'alias.md'
        alias.symlink_to(self.output)
        with self.assertRaises(ValueError):
            completion.complete(self.root, 'system/intake/S9.md', ['atlas/meetings/alias.md'], self.receipts)
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertEqual(list(self.receipts.iterdir()), [])

    def test_outside_receipt_directory_is_rejected(self):
        with self.assertRaises(ValueError):
            completion.complete(self.root, 'system/intake/S9.md', ['atlas/meetings/M9.md'], self.root)
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_numeric_processing_flag_is_not_false(self):
        self.source.write_bytes(self.raw.replace(b'processed: false', b'processed: 0'))
        with self.assertRaises(ValueError): self.run_completion()

    def test_receipt_revalidation_passes_unchanged_outputs(self):
        receipt = self.run_completion()
        self.assertTrue(completion.verify_receipt(self.root, receipt)['complete'])

    def test_receipt_revalidation_rejects_changed_output(self):
        receipt = self.run_completion()
        self.output.write_text('Later project change.\n')
        with self.assertRaises(ValueError): completion.verify_receipt(self.root, receipt)
        self.assertEqual(self.output.read_text(), 'Later project change.\n')

    def test_receipt_revalidation_rejects_changed_source(self):
        receipt = self.run_completion()
        destination = self.root/'system/transcripts/S9.md'
        destination.write_text('Changed after completion.\n')
        with self.assertRaises(ValueError): completion.verify_receipt(self.root, receipt)

    def test_receipt_revalidation_rejects_returned_intake_copy(self):
        receipt = self.run_completion()
        self.source.write_bytes(self.raw)
        with self.assertRaises(ValueError): completion.verify_receipt(self.root, receipt)

    def test_receipt_revalidation_rejects_incomplete_journal(self):
        receipt = self.run_completion()
        data = json.loads(receipt.read_text())
        data['complete'] = False
        receipt.write_text(json.dumps(data))
        with self.assertRaises(ValueError): completion.verify_receipt(self.root, receipt)

    def test_no_content_preserves_source_and_records_disposition(self):
        raw = b'---\nsource-id: voicemail\nprocessed: false\n---\nYou have reached the voicemail inbox.\n'
        self.source.write_bytes(raw)
        before = self.output.read_bytes()
        receipt = completion.complete(self.root, 'system/intake/S9.md', [], self.receipts,
                                      'no-content', 'Recording contains only a voicemail greeting.')
        data = completion.verify_receipt(self.root, receipt)
        self.assertEqual(data['disposition'], 'no-content')
        self.assertEqual(data['output_hashes'], {})
        self.assertEqual(self.output.read_bytes(), before)
        self.assertEqual((receipt.parent/'source-before.snapshot').read_bytes(), raw)
        self.assertTrue((self.root/'system/transcripts/S9.md').read_bytes().endswith(b'You have reached the voicemail inbox.\n'))

    def test_no_content_requires_reason(self):
        with self.assertRaises(ValueError):
            completion.complete(self.root, 'system/intake/S9.md', [], self.receipts, 'no-content')
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_no_content_rejects_conflicting_outputs(self):
        with self.assertRaises(ValueError):
            completion.complete(self.root, 'system/intake/S9.md', ['atlas/meetings/M9.md'], self.receipts,
                                'no-content', 'No substantive content.')
        self.assertEqual(self.source.read_bytes(), self.raw)

    def interrupt_after_archive(self):
        actual = completion.verify
        calls = []
        def interrupt(root, outputs, source):
            calls.append(source)
            if len(calls) == 2: raise SystemExit('Interrupted')
            actual(root, outputs, source)
        with patch.object(completion, 'verify', side_effect=interrupt):
            with self.assertRaises(SystemExit): self.run_completion()
        return next(self.receipts.glob('*/receipt.json'))

    def test_recovery_completes_interrupted_archive(self):
        old = self.interrupt_after_archive()
        before_output = self.output.read_bytes()
        receipt = completion.resume(self.root, old)
        self.assertNotEqual(receipt, old)
        self.assertTrue(completion.verify_receipt(self.root, receipt)['complete'])
        self.assertEqual(self.output.read_bytes(), before_output)
        self.assertFalse(json.loads(old.read_text())['complete'])
        self.assertTrue(list(self.receipts.glob('recovery-*/archive-before.snapshot')))

    def test_recovery_refuses_changed_outputs(self):
        old = self.interrupt_after_archive()
        self.output.write_text('New work from another agent.\n')
        archive = self.root/'system/transcripts/S9.md'
        before = archive.read_bytes()
        with self.assertRaises(ValueError): completion.resume(self.root, old)
        self.assertEqual(archive.read_bytes(), before)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.output.read_text(), 'New work from another agent.\n')

    def test_recovery_refuses_changed_archive(self):
        old = self.interrupt_after_archive()
        archive = self.root/'system/transcripts/S9.md'
        archive.write_text('Reconciled by another agent.\n')
        with self.assertRaises(ValueError): completion.resume(self.root, old)
        self.assertEqual(archive.read_text(), 'Reconciled by another agent.\n')
        self.assertFalse(self.source.exists())

    def test_recovery_of_completed_receipt_is_read_only(self):
        receipt = self.run_completion()
        before = {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(completion.resume(self.root, receipt).resolve(), receipt.resolve())
        after = {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

if __name__ == '__main__': unittest.main()
