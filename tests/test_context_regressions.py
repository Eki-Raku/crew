"""Hook deadlines, executable instructions, and host namespace regressions."""
import argparse
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from crew import context_cli, context_hooks, context_store as cs, core, hosts


class ContextRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "project with ' quotes"
        self.project.mkdir()
        (self.project / '.crew').mkdir()
        (self.project / '.crew' / 'config.json').write_text('{"schema_version": 1, "roles": {}}')
        self.env = patch.dict(os.environ, {
            'CREW_DATA_HOME': str(self.root / 'data'),
            'CODEX_HOME': str(self.root / 'codex'),
            'CLAUDE_CONFIG_DIR': str(self.root / 'claude'),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.host = patch.object(hosts, 'CURRENT', 'codex')
        self.host.start()
        self.addCleanup(self.host.stop)

    def test_git_timeout_never_changes_scope_or_creates_db(self):
        with patch.object(cs.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', .01)):
            with self.assertRaisesRegex(cs.ContextError, 'different scope'):
                cs.ContextStore(self.project, deadline=time.monotonic() + .01)
        self.assertFalse((self.root / 'data').exists())

    def test_scope_detection_receives_remaining_budget(self):
        with patch.object(cs.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', '')) as run:
            cs.ContextStore(self.project, deadline=time.monotonic() + .2, busy_timeout=.05)
        self.assertGreater(run.call_args.kwargs['timeout'], 0)
        self.assertLessEqual(run.call_args.kwargs['timeout'], .2)

    def test_current_schema_initialization_is_read_only_under_writer(self):
        store = cs.ContextStore(self.project)
        with sqlite3.connect(store.path) as locked:
            locked.execute('BEGIN IMMEDIATE')
            started = time.monotonic()
            reopened = cs.ContextStore(self.project, deadline=started + .5, busy_timeout=.05)
            self.assertEqual(reopened.stats()['tasks'], store.stats()['tasks'])
            self.assertLess(time.monotonic() - started, .5)

    def test_hook_degrades_quickly_under_writer_lock(self):
        store = cs.ContextStore(self.project)
        with sqlite3.connect(store.path) as locked:
            locked.execute('BEGIN IMMEDIATE')
            started = time.monotonic()
            result = context_hooks.handle({'hook_event_name': 'SessionStart', 'session_id': 'busy'}, self.project)
            self.assertLess(time.monotonic() - started, 1)
        self.assertIn('unavailable', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(store.list_tasks(), [])

    def test_shared_deadline_expires_between_connections(self):
        store = cs.ContextStore(self.project, deadline=time.monotonic() + 1, busy_timeout=.05)
        store.open_session('codex', 'one')
        store.deadline = time.monotonic() - .01
        for operation in (lambda: store.open_session('codex', 'two'),
                          lambda: store.decision_list(scope='global')):
            with self.assertRaisesRegex(cs.ContextError, 'deadline'):
                operation()

    def test_hook_commands_quote_interpreter_script_host_and_project(self):
        with patch.object(sys, 'executable', "/python dir/python's"):
            command = context_hooks.context_command(self.project, 'read', '--task', 'ctx_123')
        self.assertEqual(shlex.split(command), [
            "/python dir/python's", str(Path(context_hooks.__file__).resolve().parents[1] / 'bin' / 'crew'),
            '--host', 'codex', 'context', '--project', str(self.project.resolve()),
            'read', '--task', 'ctx_123'])
        result = context_hooks.handle({'hook_event_name': 'SessionStart'}, self.project)
        self.assertIn(context_hooks.context_command(self.project, 'open', '--session', '<session-id>'),
                      result['hookSpecificOutput']['additionalContext'])
        with patch.object(cs, 'ContextStore', side_effect=cs.ContextError('busy')):
            result = context_hooks.handle({'hook_event_name': 'SessionStart', 'session_id': 'one'}, self.project)
        self.assertIn(context_hooks.context_command(self.project, 'stats'),
                      result['hookSpecificOutput']['additionalContext'])

    def test_cli_provider_mismatch_rejected_before_store_open(self):
        with patch.object(cs, 'ContextStore') as store:
            with self.assertRaisesRegex(core.Error, '--host claude'):
                context_cli.run(argparse.Namespace(provider='claude', action='open'), self.project)
        store.assert_not_called()

    def test_interactive_provider_mismatch_rejected_for_open_and_state(self):
        for choice in ('2', '7'):
            answers = iter([choice, 'claude', '0'])
            with patch.object(cs, 'ContextStore') as store, patch.object(context_cli, 'say') as say:
                context_cli.menu(self.project, lambda *args: next(answers), lambda *args: None)
            store.return_value.open_session.assert_not_called()
            store.return_value.session_state.assert_not_called()
            self.assertTrue(any('--host claude' in str(call) for call in say.call_args_list))

    def test_explicit_data_root_allows_parent_alias_but_not_leaf_alias(self):
        real = self.root / 'real-parent'; real.mkdir()
        alias = self.root / 'parent-alias'; alias.symlink_to(real, target_is_directory=True)
        store = cs.ContextStore(self.project, data_home=alias / 'data')
        self.assertEqual(store.path.parent, real / 'data')
        self.assertTrue(store.path.is_file())
        (real / 'linked-data').symlink_to(real / 'data', target_is_directory=True)
        with self.assertRaises(core.Error):
            cs.ContextStore(self.project, data_home=alias / 'linked-data')

    def test_sidecar_disappearing_during_check_is_not_a_false_error(self):
        store = cs.ContextStore(self.project)
        wal = Path(str(store.path) + '-wal'); wal.write_bytes(b'')
        original = Path.lstat
        observed = 0
        def disappearing(path, *args, **kwargs):
            nonlocal observed
            if path == wal:
                observed += 1
                if observed == 2: wal.unlink()
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', disappearing): store._check_paths()
        self.assertFalse(wal.exists())
        self.assertGreaterEqual(observed, 2)

    def test_nonregular_database_sidecar_is_rejected(self):
        store = cs.ContextStore(self.project)
        wal = Path(str(store.path) + '-wal'); os.mkfifo(wal)
        with self.assertRaisesRegex(cs.ContextError, 'regular file'): store._check_paths()

    def test_database_and_sidecar_symlinks_are_rejected(self):
        for suffix in ('', '-wal', '-shm', '-journal'):
            with self.subTest(suffix=suffix):
                data = self.root / ('db' + (suffix or '-main'))
                data.mkdir()
                target = self.root / ('target' + (suffix or '-main'))
                target.write_text('private')
                (data / ('context.sqlite3' + suffix)).symlink_to(target)
                with self.assertRaises(core.Error):
                    cs.ContextStore(self.project, data)
                self.assertEqual(target.read_text(), 'private')

    def test_data_directory_symlink_is_rejected(self):
        target = self.root / 'outside'
        target.mkdir()
        (self.root / 'data').symlink_to(target, target_is_directory=True)
        with self.assertRaises(core.Error):
            cs.ContextStore(self.project)
        self.assertEqual(list(target.iterdir()), [])

    def test_direct_provider_namespace_api_is_unchanged(self):
        store = cs.ContextStore(self.project)
        a = store.open_session('codex', 'one')
        b = store.open_session('claude', 'one')
        self.assertNotEqual(a['task_id'], b['task_id'])


if __name__ == '__main__':
    unittest.main()
