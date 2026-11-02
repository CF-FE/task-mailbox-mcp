import contextlib
import io
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mac'))
from bridge_store import BridgeStore
from install import install


class MacRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.config = self.root / 'config.json'
        self.state = self.root / 'state'
        self.config.write_text(json.dumps({'schema_version': 1,
            'endpoint': 'https://mcp.example.com/mailbox/mcp', 'source_id': 'test-source',
            'consumer_id': 'mac-coordinator', 'state_dir': str(self.state),
            'allowed_roots': [str(self.project)]}))

    def test_network_once_without_own_login_reports_auth_required_without_side_effects(self):
        result = subprocess.run([sys.executable, str(ROOT / 'mac/bridge.py'), '--config', str(self.config), 'once'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(1, result.returncode, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual('AUTH_REQUIRED', output['status'])
        self.assertFalse(output['execution_enabled'])
        self.assertFalse((self.state / 'oauth').exists())
        health = BridgeStore(self.state / 'bridge.sqlite3', 'test-source').snapshot()['health']
        self.assertEqual('AUTH_REQUIRED', health[0]['state'])

    def test_native_command_shape_records_only_scoped_lifecycle_metadata(self):
        event = {'hook_event_name': 'SessionStart', 'session_id': 'session-native',
                 'cwd': str(self.project), 'source': 'startup',
                 'prompt': 'DO_NOT_COPY_PROMPT', 'last_assistant_message': 'DO_NOT_COPY_REPLY'}
        result = subprocess.run([sys.executable, str(ROOT / 'mac/codex_hook.py'), '--config', str(self.config)],
                                input=json.dumps(event), capture_output=True, text=True, timeout=3)
        self.assertEqual(0, result.returncode, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual('SessionStart', output['hookSpecificOutput']['hookEventName'])
        snapshot = BridgeStore(self.state / 'bridge.sqlite3', 'test-source').snapshot()
        self.assertEqual('session-native', snapshot['sessions'][0]['session_id'])
        raw = (self.state / 'bridge.sqlite3').read_bytes()
        self.assertNotIn(b'DO_NOT_COPY_PROMPT', raw)
        self.assertNotIn(b'DO_NOT_COPY_REPLY', raw)

    def test_install_prepares_private_copies_and_preserves_other_hooks_without_starting(self):
        runtime = self.root / 'runtime'
        python = runtime / '.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('not executed')
        hook_file = self.root / 'hooks.json'
        other = {'hooks': {'PreCompact': [{'hooks': [{'type': 'command', 'command': 'keep-existing'}]}]}}
        hook_file.write_text(json.dumps(other))
        launch = self.root / 'agents/bridge.plist'
        result = install(runtime, self.state, 'https://mcp.example.com/mailbox/mcp',
                         [str(self.project)], hook_file, launch, 'fixed-test-commit')
        self.assertEqual('REVIEW_REQUIRED', result['hook_trust'])
        self.assertEqual('PREPARED_NOT_BOOTSTRAPPED', result['launch_agent'])
        self.assertEqual(other['hooks']['PreCompact'], json.loads(hook_file.read_text())['hooks']['PreCompact'])
        self.assertEqual(0o600, hook_file.stat().st_mode & 0o777)
        self.assertEqual(0o700, runtime.stat().st_mode & 0o777)
        self.assertTrue((runtime / 'app/codex_hook.py').is_file())
        with launch.open('rb') as stream:
            plist = plistlib.load(stream)
        self.assertFalse(plist['KeepAlive'])
        self.assertFalse(plist['RunAtLoad'])
        self.assertFalse(result['start_at_login'])
        self.assertFalse(json.loads((runtime / 'install-receipt.json').read_text())['start_at_login'])
        self.assertIn(str(runtime.resolve() / 'config.json'), plist['ProgramArguments'])
        # Reinstall keeps one owned hook and preserves the same scope/identity.
        install(runtime, self.state, 'https://mcp.example.com/mailbox/mcp',
                [str(self.project)], hook_file, launch, 'fixed-test-commit')
        stop = json.loads(hook_file.read_text())['hooks']['Stop']
        self.assertEqual(1, len(stop))

    def prepare_install_args(self):
        runtime = self.root / 'runtime'
        python = runtime / '.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('not executed')
        return (runtime, self.state, 'https://mcp.example.com/mailbox/mcp',
                [str(self.project)], self.root / 'hooks.json',
                self.root / 'agents/bridge.plist', 'fixed-test-commit')

    def test_install_upgrade_preserves_enabled_login_start(self):
        args = self.prepare_install_args()
        install(*args)
        launch = args[5]
        plist = plistlib.loads(launch.read_bytes())
        plist['RunAtLoad'] = True
        launch.write_bytes(plistlib.dumps(plist))

        result = install(*args)

        upgraded = plistlib.loads(launch.read_bytes())
        self.assertTrue(upgraded['RunAtLoad'])
        self.assertFalse(upgraded['KeepAlive'])
        self.assertTrue(result['start_at_login'])
        receipt = json.loads((args[0] / 'install-receipt.json').read_text())
        self.assertTrue(receipt['start_at_login'])
        self.assertEqual('PREPARED_NOT_BOOTSTRAPPED', result['launch_agent'])

    def test_install_cli_explicitly_enables_login_start_without_bootstrapping(self):
        args = self.prepare_install_args()
        runtime, state, endpoint, roots, hooks, launch, commit = args
        result = subprocess.run([sys.executable, str(ROOT / 'mac/install.py'),
            '--runtime-dir', str(runtime), '--state-dir', str(state),
            '--endpoint', endpoint, '--allowed-root', roots[0],
            '--hook-file', str(hooks), '--launch-agent', str(launch),
            '--source-commit', commit, '--start-at-login'],
            capture_output=True, text=True, timeout=5)

        self.assertEqual(0, result.returncode, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output['start_at_login'])
        self.assertFalse(output['execution_enabled'])
        self.assertEqual('PREPARED_NOT_BOOTSTRAPPED', output['launch_agent'])
        plist = plistlib.loads(launch.read_bytes())
        self.assertTrue(plist['RunAtLoad'])
        self.assertFalse(plist['KeepAlive'])
        self.assertTrue(json.loads((runtime / 'install-receipt.json').read_text())['start_at_login'])
        self.assertEqual('not executed', (runtime / '.venv/bin/python').read_text())

    def test_install_rejects_mismatched_existing_launch_unit_before_replacing_files(self):
        args = self.prepare_install_args()
        runtime, _, _, _, hooks, launch, _ = args
        launch.parent.mkdir(parents=True)
        existing = {'Label': 'io.taskmailbox.coordinator',
                    'ProgramArguments': ['/another/runtime/bridge.py'],
                    'WorkingDirectory': '/another/runtime', 'RunAtLoad': True}
        before = plistlib.dumps(existing)
        launch.write_bytes(before)

        with self.assertRaisesRegex(ValueError, 'launch agent.*differs'):
            install(*args)

        self.assertEqual(before, launch.read_bytes())
        self.assertFalse((runtime / 'config.json').exists())
        self.assertFalse((runtime / 'app').exists())
        self.assertFalse(hooks.exists())


if __name__ == '__main__':
    unittest.main()
