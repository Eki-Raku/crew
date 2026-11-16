"""Regressions for the code-review fixes: receipts, scope stability, hook cost, workflow evidence."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from crew import context_store as cs, core, hosts, workflow_hooks
from crew.workflow import WorkflowStore, dirty_paths, fingerprint


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir(); (self.project / '.crew').mkdir()
        (self.project / '.crew' / 'config.json').write_text('{"schema_version": 1, "roles": {}}')
        self.env = patch.dict(os.environ, {'CODEX_HOME': str(self.root / 'home'), 'CREW_DATA_HOME': str(self.root / 'data'),
                                           'CREW_HOST': 'codex', 'CREW_LANG': 'en'})
        self.env.start(); hosts.select('codex')
        self.addCleanup(self.env.stop); self.addCleanup(self.tmp.cleanup)


class ReceiptTests(Base):
    def test_damaged_receipt_is_reset_not_fatal(self):
        p = core.receipt_path(self.project)
        p.parent.mkdir(parents=True); p.write_text('{not json')
        core.record({'hook_event_name': 'SessionStart', 'session_id': 's'}, self.project)
        self.assertEqual(len(json.loads(p.read_text())['events']), 1)
        p.write_text('{"events": 5}')
        self.assertEqual(core.doctor(self.project)['hook_receipts']['events'], [])
        core.record({'hook_event_name': 'PostCompact', 'trigger': 'auto', 'session_id': 's'}, self.project)
        self.assertEqual(json.loads(p.read_text())['auto_compactions'], {'s': 1})

    def test_agents_backups_are_pruned(self):
        target = core.agents_target(); target.parent.mkdir(parents=True)
        target.write_text('# mine\n')
        for i in range(8):
            Path(str(target) + '.crew-backup-' + str(1000 + i)).write_text('old')
            core.setup(apply=True)
            target.write_text('# mine\n' + str(i) + '\n')
        backups = list(target.parent.glob('AGENTS.md.crew-backup-*'))
        self.assertLessEqual(len(backups), core.BACKUPS_KEPT)

    def test_doctor_flags_missing_cli_path(self):
        target = core.agents_target(); target.parent.mkdir(parents=True)
        target.write_text(core.START + '\nBefore project work, run the Crew CLI at "/gone/bin/crew" with x\n' + core.END + '\n')
        info = core.doctor(self.project)
        self.assertFalse(info['entry_command_exists'])
        self.assertTrue(info['warnings'])


class ScopeTests(Base):
    def test_git_failure_inside_checkout_does_not_fork_scope(self):
        (self.project / '.git').mkdir()
        failed = subprocess.CompletedProcess([], 128, '', 'fatal: detected dubious ownership')
        with patch.object(cs.subprocess, 'run', return_value=failed):
            with self.assertRaisesRegex(cs.ContextError, 'different scope'):
                cs.project_scope(self.project)
        with patch.object(cs.subprocess, 'run', side_effect=FileNotFoundError('git')):
            with self.assertRaisesRegex(cs.ContextError, 'different scope'):
                cs.project_scope(self.project)

    def test_plain_directory_still_uses_directory_scope(self):
        failed = subprocess.CompletedProcess([], 128, '', 'fatal: not a git repository')
        with patch.object(cs.subprocess, 'run', return_value=failed):
            self.assertTrue(cs.project_scope(self.project)[1].startswith('directory:'))


class StoreCostTests(Base):
    def test_unchanged_open_does_not_take_write_lock(self):
        store = cs.ContextStore(self.project)
        first = store.open_session('codex', 's1')
        writes = []
        original = store._transaction
        def spy(write=False):
            writes.append(write); return original(write)
        with patch.object(store, '_transaction', spy):
            again = store.open_session('codex', 's1')
        self.assertEqual(again['task_id'], first['task_id'])
        self.assertFalse(again['created']); self.assertFalse(again['reset'])
        self.assertNotIn(True, writes)

    def test_stale_or_changed_open_still_writes(self):
        store = cs.ContextStore(self.project)
        task = store.open_session('codex', 's1')['task_id']
        other = store.open_session('codex', 's2')['task_id']
        self.assertEqual(store.open_session('codex', 's1', task_id=other)['task_id'], other)
        store.delete(other)
        self.assertTrue(store.open_session('codex', 's1')['reset'])
        self.assertNotEqual(task, other)

    def test_decision_digest_has_no_values(self):
        store = cs.ContextStore(self.project)
        store.decision_put('a', 'secret value')
        self.assertEqual(store.decision_digest(), [{'key': 'a', 'version': 1}])
        store.decision_delete('a', expected_version=1)
        self.assertEqual(store.decision_digest(), [])


class WorkflowEvidenceTests(Base):
    def setUp(self):
        super().setUp()
        (self.project / '.git').mkdir()
        for role in ('implementer', 'reviewer'):
            core.role_write('project', self.project, role, dict(core.presets('en')[role], model='m', effort='low'), 'create')
        self.store = WorkflowStore(self.project)
        self.task = self.store.context.open_session('codex', 'parent')['task_id']
        self.store.start(self.task, 'parent', 'goal', 'implementer', 'reviewer')

    def test_git_directory_is_rejected_case_insensitively(self):
        for name in ('.git/config', '.GIT/config', 'sub/.Git/x'):
            with self.assertRaises(core.Error): fingerprint(self.project, [name])

    def test_stop_correction_does_not_invalidate_coordinator_version(self):
        version = self.store.read(self.task)['version']
        event = dict(hook_event_name='Stop', session_id='parent')
        self.assertEqual(workflow_hooks.handle(event, self.project)['decision'], 'block')
        self.assertEqual(self.store.read(self.task)['version'], version)
        self.store.change(self.task, version, 'dispatch')  # would raise ConflictError before the fix

    def test_irrelevant_tool_events_skip_the_store(self):
        with patch.object(workflow_hooks, 'WorkflowStore', side_effect=AssertionError('opened')):
            for name in ('PreToolUse', 'PostToolUse'):
                self.assertEqual(workflow_hooks.handle(dict(hook_event_name=name, session_id='parent', tool_name='shell'), self.project), {})

    def test_dirty_paths_parse_and_skip_crew_state(self):
        repo = self.root / 'repo'; repo.mkdir()
        run = lambda *a: subprocess.run(['git', '-C', str(repo), *a], check=True, capture_output=True)
        run('init'); run('config', 'user.email', 'a@b.c'); run('config', 'user.name', 'n')
        (repo / 'kept.txt').write_text('1'); (repo / 'old name.txt').write_text('2'); run('add', '.'); run('commit', '-m', 'x')
        (repo / 'kept.txt').write_text('changed'); (repo / 'new.txt').write_text('n')
        (repo / '.crew').mkdir(); (repo / '.crew' / 'config.json').write_text('{}')
        run('mv', 'old name.txt', 'new name.txt')
        self.assertEqual(dirty_paths(repo), ['kept.txt', 'new name.txt', 'new.txt', 'old name.txt'])  # a rename lists both sides
        self.assertEqual(dirty_paths(self.root / 'missing'), [])


if __name__ == '__main__':
    unittest.main()
