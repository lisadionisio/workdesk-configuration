import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('extraction_validator', ROOT / 'config/scripts/validate-extraction.py')
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)
SCHEMA = json.loads((ROOT / 'config/skills/process-transcripts/schema.json').read_text())


def valid_output():
    return {'summary': 'Sam will send a draft.', 'attendees_present': ['Sam'],
            'speaker_resolution_confidence': 'high', 'key_topics': [], 'decisions': [],
            'action_items': [{'description': 'Send draft', 'commitment_status': 'committed', 'owner': 'Sam',
                              'owner_category': 'unknown', 'due': 'tomorrow',
                              'blocks_operator': False}],
            'open_questions': [], 'sensitive': False}


def response(value=None, text=None):
    return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
        {'text': text if text is not None else json.dumps(value if value is not None else valid_output())}
    ]}}]}


class ContractTests(unittest.TestCase):
    def test_bundled_schema_and_valid_nested_output(self):
        validator.check_schema(SCHEMA)
        self.assertEqual(validator.extract(response(), SCHEMA), valid_output())

    def test_missing_null_wrong_types_and_unknown_fields(self):
        changes = [None, [], {}, {'sensitive': 'false'}, {'attendees_present': ['Sam', 7]},
                   {'action_items': [{'description': 'Draft', 'commitment_status': 'committed', 'owner': 'Sam', 'owner_category': 'employee'}]},
                   {'action_items': [{'description': 'Draft', 'commitment_status': 'committed', 'owner_category': 'unknown'}]},
                   {'extra_unchecked_claim': 'Invented'}, {'sensitive': 0}]
        for change in changes:
            with self.subTest(change=change):
                value = valid_output()
                if isinstance(change, dict) and change:
                    value.update(change)
                else:
                    value = change
                with self.assertRaises(ValueError):
                    validator.extract(response(text=json.dumps(value)), SCHEMA)

    def test_assignment_status_is_required_and_preserved(self):
        value = valid_output()
        value['action_items'][0].update(description='Assigned review, acceptance unconfirmed',
                                        commitment_status='assigned-unconfirmed')
        self.assertEqual(validator.extract(response(value=value), SCHEMA), value)
        for status in [None, 'approved', '', False]:
            invalid = copy.deepcopy(value)
            if status is None:
                del invalid['action_items'][0]['commitment_status']
            else:
                invalid['action_items'][0]['commitment_status'] = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                validator.extract(response(value=invalid), SCHEMA)

    def test_duplicate_properties_multiple_documents_and_non_json_constants(self):
        for text in ['{"summary":"one","summary":"two"}', '{} {}', 'NaN', 'null', 'false', '']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                validator.extract(response(text=text), SCHEMA)

    def test_nonfinal_blocked_and_missing_provider_results(self):
        for reason in ['MAX_TOKENS', 'SAFETY', None, 'FINISH_REASON_UNSPECIFIED']:
            value = response()
            value['candidates'][0]['finishReason'] = reason
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                validator.extract(value, SCHEMA)
        for value in [{}, {'candidates': []}, {'error': {}}, {'candidates': [None]},
                      {'promptFeedback': {'blockReason': 'SAFETY'}}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validator.extract(value, SCHEMA)

    def test_combines_final_parts_and_ignores_thought_text(self):
        text = json.dumps(valid_output())
        value = response()
        value['candidates'][0]['content']['parts'] = [
            {'thought': True, 'text': 'Non-output reasoning'},
            {'text': text[:20]}, {'text': text[20:]}]
        self.assertEqual(validator.extract(value, SCHEMA), valid_output())
        value['candidates'][0]['content']['parts'] = [{'thought': True, 'text': 'Only thought'}]
        with self.assertRaises(ValueError):
            validator.extract(value, SCHEMA)

    def test_schema_drift_cannot_silently_drop_constraints(self):
        for key, value in [('minimum', 1), ('additionalProperties', False), ('oneOf', [])]:
            schema = copy.deepcopy(SCHEMA)
            schema['properties']['summary'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validator.check_schema(schema)


class ShellBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='workdesk-extraction-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        scripts = self.root / 'config/scripts'
        (scripts / 'lib').mkdir(parents=True)
        for name in ['extract-transcript-gemini.sh', 'validate-extraction.py']:
            shutil.copy2(ROOT / 'config/scripts' / name, scripts / name)
        (scripts / 'lib/resolve-secret.sh').write_text("wd_resolve_secret() { printf '%s' 'AIzaSyntheticFixtureOnly'; }\n")
        skill = self.root / 'config/skills/process-transcripts'
        skill.mkdir(parents=True)
        for name in ['schema.json', 'prompt.txt']:
            shutil.copy2(ROOT / 'config/skills/process-transcripts' / name, skill / name)
        self.source = self.root / 'source.md'
        self.source.write_text('---\nsource-kind: transcript\n---\nSam: I will send a draft tomorrow.\n')
        self.before = self.source.read_bytes()
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        curl = bin_dir / 'curl'
        curl.write_text(f'#!{sys.executable}\n' + '''import os,sys
from pathlib import Path
args=sys.argv[1:]
Path(os.environ['REQUEST_CAPTURE']).write_text(args[args.index('-d')+1])
Path(args[args.index('-o')+1]).write_bytes(Path(os.environ['RESPONSE_FIXTURE']).read_bytes())
print(os.environ.get('MOCK_HTTP_CODE','200'),end='')
raise SystemExit(int(os.environ.get('MOCK_CURL_EXIT','0')))
''')
        curl.chmod(0o755)
        self.env = dict(os.environ, PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],
                        RESPONSE_FIXTURE=str(self.root/'response.json'), REQUEST_CAPTURE=str(self.root/'request.json'))
        self.command = ['bash', str(scripts/'extract-transcript-gemini.sh'), str(self.source)]

    def run_response(self, value):
        (self.root/'response.json').write_text(json.dumps(value))
        result = subprocess.run(self.command, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertNotIn('AIzaSyntheticFixtureOnly', result.stdout+result.stderr)
        return result

    def test_canonical_resources_work_without_claude_adapter(self):
        self.assertFalse((self.root/'.claude').exists())
        payload = response()
        payload.update(modelVersion='synthetic-provider-revision', responseId='synthetic-response-id',
                       usageMetadata={'promptTokenCount': 12, 'candidatesTokenCount': 8, 'totalTokenCount': 20})
        result = self.run_response(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), valid_output())
        self.assertEqual(json.loads(result.stderr), {'prompt': 12, 'output': 8, 'total': 20,
                                                    'modelVersion': 'synthetic-provider-revision',
                                                    'responseId': 'synthetic-response-id'})
        request = json.loads((self.root/'request.json').read_text())
        self.assertEqual(request['generationConfig']['responseSchema'], SCHEMA)
        self.assertIn('Sam: I will send a draft tomorrow.', request['contents'][0]['parts'][0]['text'])

    def test_invalid_provider_payload_never_emits_success_json(self):
        for value in [response(text='null'), response(value={}), {}, response(text='{} {}')]:
            with self.subTest(value=value):
                result = self.run_response(value)
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertEqual(result.stdout, '')

    def test_invalid_schema_fails_before_transport(self):
        (self.root/'config/skills/process-transcripts/schema.json').write_text('{}')
        result = self.run_response(response())
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root/'request.json').exists())

    def test_failed_transport_cannot_succeed_with_complete_body(self):
        self.env['MOCK_CURL_EXIT'] = '28'
        result = self.run_response(response())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('transport failed', result.stderr)

    def test_http_and_api_errors_never_emit_success(self):
        self.env['MOCK_HTTP_CODE'] = '429'
        result = self.run_response({'error': {'code': 429, 'message': 'Synthetic rate limit'}})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.env['MOCK_HTTP_CODE'] = '200'
        result = self.run_response({'error': {'code': 400, 'message': 'Synthetic API error'}})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')

    def test_body_separators_survive_frontmatter_removal(self):
        body = 'Sam: First section.\n---\nSam: Second section.\n---\nAlex: Third section.'
        self.source.write_text('---\nsource-kind: transcript\n---\n'+body+'\n')
        self.before = self.source.read_bytes()
        result = self.run_response(response())
        self.assertEqual(result.returncode, 0, result.stderr)
        request = json.loads((self.root/'request.json').read_text())
        self.assertTrue(request['contents'][0]['parts'][0]['text'].endswith(body))

    def test_incomplete_frontmatter_fails_before_transport(self):
        self.source.write_text('---\nsource-kind: transcript\nSam: No closing delimiter.\n')
        self.before = self.source.read_bytes()
        result = self.run_response(response())
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root/'request.json').exists())

    def test_missing_model_argument_is_hard_failure(self):
        result = subprocess.run(self.command+['--model'], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root/'request.json').exists())


if __name__ == '__main__':
    unittest.main()
