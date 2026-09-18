import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gws_account', ROOT/'config/scripts/lib/gws_account.py')
gws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gws)
ACCOUNT = 'operator@example.test'


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workdesk-gws-account-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []

    def env(self, mode='config-dir'):
        route = {'mode': mode}
        if mode == 'config-dir':
            route['config_dir'] = str(self.root)
        return dict(WORKDESK_GWS_ACCOUNTS=json.dumps({ACCOUNT: route}), PATH='/usr/bin:/bin',
                    **{k: 'competing-value' for k in gws.IDENTITY_ENV})

    def runner(self, version='0.22.5', identity=None, status=0):
        identity = {'user': {'emailAddress': ACCOUNT}} if identity is None else identity
        def run(args, **kwargs):
            self.calls.append((args, kwargs))
            if args[1:] == ['--version']:
                return SimpleNamespace(returncode=0, stdout='gws '+version+'\nDisclaimer\n')
            return SimpleNamespace(returncode=status, stdout=json.dumps(identity))
        return run

    def test_both_verified_layouts_and_environment_isolation(self):
        for mode, version, selected in [('config-dir', '0.22.5', 'GOOGLE_WORKSPACE_CLI_CONFIG_DIR'),
                                        ('legacy-account', '0.4.1', 'GOOGLE_WORKSPACE_CLI_ACCOUNT')]:
            with self.subTest(mode=mode):
                before = self.env(mode)
                after = gws.verified_environment('/usr/bin/true', ACCOUNT, before, self.runner(version))
                self.assertEqual(before[selected], 'competing-value')
                self.assertEqual(after['PATH'], before['PATH'])
                for key in gws.IDENTITY_ENV:
                    if key != selected:
                        self.assertNotIn(key, after)
                self.assertEqual(self.calls[-1][0][1:4], ['drive', 'about', 'get'])
                self.assertEqual(self.calls[-1][1]['env'], after)

    def test_wrong_account_and_provider_errors_never_pass(self):
        for payload, status in [({'user': {'emailAddress': 'other@example.test'}}, 0),
                                ({'user': {'emailAddress': ACCOUNT}, 'error': 'sensitive-response'}, 0),
                                ({'user': {'emailAddress': ACCOUNT}}, 1),
                                ({'user': {'emailAddress': None}}, 0), ([], 0)]:
            with self.subTest(payload=payload):
                with self.assertRaises(gws.AccountError) as caught:
                    gws.verified_environment('/usr/bin/true', ACCOUNT, self.env(), self.runner(identity=payload, status=status))
                self.assertNotIn('sensitive-response', str(caught.exception))

    def test_unknown_or_mismatched_version_stops_before_identity(self):
        for version in ('0.4.1', '0.23.0', 'unknown'):
            self.calls = []
            with self.assertRaises(gws.AccountError):
                gws.verified_environment('/usr/bin/true', ACCOUNT, self.env(), self.runner(version))
            self.assertEqual(len(self.calls), 1)

    def test_duplicate_or_missing_routes_fail_before_process(self):
        for raw in ['{}', '[]', '{"'+ACCOUNT+'":{},"'+ACCOUNT+'":{}}', 'not-json']:
            with self.assertRaises(gws.AccountError):
                gws.verified_environment('/usr/bin/true', ACCOUNT, {'WORKDESK_GWS_ACCOUNTS': raw}, self.runner())
        self.assertEqual(self.calls, [])

    def test_route_shapes_and_paths_are_strict(self):
        for route in [{'mode':'config-dir','config_dir':'relative'}, {'mode':'config-dir','config_dir':str(self.root/'absent')},
                      {'mode':'config-dir','config_dir':False}, {'mode':'legacy-account','extra':True},
                      {'mode':'default'}, None]:
            with self.assertRaises(gws.AccountError):
                gws.account_route(ACCOUNT, {'WORKDESK_GWS_ACCOUNTS':json.dumps({ACCOUNT:route})})

    def test_host_local_file_is_used(self):
        (self.root/'gws-accounts.json').write_text(json.dumps({ACCOUNT:{'mode':'legacy-account'}}))
        mode, env = gws.account_route(ACCOUNT, {'WORKDESK_STATE_HOME': str(self.root)})
        self.assertEqual(mode,'legacy-account')
        self.assertEqual(env['GOOGLE_WORKSPACE_CLI_ACCOUNT'], ACCOUNT)

    def test_missing_route_file_has_safe_error(self):
        with self.assertRaises(gws.AccountError):
            gws.account_route(ACCOUNT, {'WORKDESK_STATE_HOME':str(self.root)})

    def test_timeout_and_malformed_provider_output(self):
        for error in [subprocess.TimeoutExpired(['gws'],90), OSError('sensitive-diagnostic')]:
            def run(*args, **kwargs): raise error
            with self.assertRaises(gws.AccountError) as caught:
                gws.verified_environment('/usr/bin/true', ACCOUNT, self.env(), run)
            self.assertNotIn('sensitive-diagnostic', str(caught.exception))
        def duplicate(args, **kwargs):
            return SimpleNamespace(returncode=0, stdout='gws 0.22.5' if args[1]=='--version' else '{"user":{},"user":{}}')
        with self.assertRaises(gws.AccountError):
            gws.verified_environment('/usr/bin/true', ACCOUNT, self.env(), duplicate)

    def test_invalid_binary_or_email_does_not_run(self):
        for binary, account in [('gws',ACCOUNT), (str(self.root),ACCOUNT), ('/usr/bin/true','not-email')]:
            with self.assertRaises(gws.AccountError):
                gws.verified_environment(binary,account,self.env(),self.runner())
        self.assertEqual(self.calls,[])

    def test_selector_is_consumed_and_explicit_account_wins(self):
        args=['gmail','users','messages','list','--params','{"text":"--account"}']
        for selector in [['--account',ACCOUNT], ['--account='+ACCOUNT]]:
            account, remaining=gws.select_command(args+selector, {'GOOGLE_WORKSPACE_CLI_ACCOUNT':'other@example.test'})
            self.assertEqual(account,ACCOUNT)
            self.assertEqual(remaining,args)
        self.assertEqual(gws.select_command(args,{'WORKDESK_GWS_ACCOUNT':ACCOUNT})[0],ACCOUNT)
        self.assertEqual(gws.select_command(args,{'GOOGLE_WORKSPACE_CLI_ACCOUNT':ACCOUNT})[0],ACCOUNT)

    def test_ambiguous_selectors_and_auth_commands_are_rejected(self):
        for args in [['drive','--account'],['drive','--account='],
                     ['drive','--account',ACCOUNT,'--account='+ACCOUNT],
                     ['auth','login','--account',ACCOUNT], []]:
            with self.assertRaises(gws.AccountError):
                gws.select_command(args,{})

if __name__ == '__main__': unittest.main(verbosity=2)
