import concurrent.futures
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crew import context_store as cs


class ContextStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / '.crew').mkdir()
        (self.project / '.crew' / 'config.json').write_text('{}')
        self.data = self.root / 'data'
        self.store = cs.ContextStore(self.project, self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def new(self, sid='one', provider='codex', **kwargs):
        return self.store.open_session(provider, sid, **kwargs)

    def test_first_open_is_empty_and_repeat_is_stable(self):
        first = self.new(title='Small task')
        again = self.new()
        self.assertTrue(first['created'])
        self.assertFalse(first['reset'])
        self.assertEqual(first['state'], {})
        self.assertFalse(again['created'])
        self.assertEqual(first['task_id'], again['task_id'])
        self.assertEqual(first['version'], again['version'])
        self.assertEqual(again['title'], 'Small task')

    def test_separate_sessions_and_explicit_sharing(self):
        one, two = self.new(), self.new('two')
        self.assertNotEqual(one['task_id'], two['task_id'])
        shared = self.new('other-agent', task_id=one['task_id'], provider='claude')
        self.assertEqual(shared['task_id'], one['task_id'])
        self.assertEqual(next(t for t in self.store.list_tasks() if t['task_id'] == one['task_id'])['session_count'], 2)
        # A provider's identity is not another provider's session.
        self.assertNotEqual(self.new(provider='claude')['task_id'], one['task_id'])

    def test_patch_and_cas_conflict(self):
        task = self.new()
        updated = self.store.patch(task['task_id'], 1, {'goal': 'Ship small context', 'next': ['Test']})
        self.assertEqual(updated['version'], 2)
        with self.assertRaises(cs.ConflictError) as caught:
            self.store.patch(task['task_id'], 1, {'goal': 'Overwrite'})
        self.assertEqual(caught.exception.current_version, 2)
        self.assertEqual(self.store.read(task['task_id'])['state']['goal'], 'Ship small context')

    def test_incremental_read_coalesces_and_reports_removals(self):
        tid = self.new()['task_id']
        self.store.patch(tid, 1, {'summary': 'First', 'goal': 'Ship'})
        self.store.patch(tid, 2, {'summary': 'Second', 'goal': None, 'next': ['Test']})
        delta = self.store.read(tid, since=1)
        self.assertEqual(delta['version'], 3)
        self.assertEqual(delta['changes'], {'summary': 'Second', 'next': ['Test']})
        self.assertEqual(delta['removed'], ['goal'])
        self.assertNotIn('state', delta)
        self.assertEqual(self.store.read(tid, 3)['changes'], {})
        self.assertEqual(self.store.read(tid, 3)['removed'], [])
        with self.assertRaises(cs.ConflictError):
            self.store.read(tid, 4)

    def test_checkpoint_replaces_compact_state(self):
        tid = self.new()['task_id']
        self.store.patch(tid, 1, {'goal': 'Ship', 'progress': ['a', 'b'], 'summary': 'Old'})
        self.store.checkpoint(tid, 2, {'summary': 'Compact'})
        self.assertEqual(self.store.read(tid)['state'], {'summary': 'Compact'})
        delta = self.store.read(tid, 2)
        self.assertEqual(delta['removed'], ['goal', 'progress'])
        self.assertEqual(delta['changes'], {'summary': 'Compact'})
        with self.assertRaises(cs.ContextError):
            self.store.checkpoint(tid, 3, {'summary': None})

    def test_deleted_then_continued_session_is_new_empty_record(self):
        old = self.new(title='Secret old title')
        tid = old['task_id']
        self.store.patch(tid, 1, {'summary': 'Do not resurrect', 'decisions': ['Old decision']}, 'old-write')
        deleted = self.store.delete(tid)
        self.assertTrue(deleted['recoverable'])
        fresh = self.new(task_id=tid)
        self.assertTrue(fresh['created'])
        self.assertTrue(fresh['reset'])
        self.assertNotEqual(fresh['task_id'], tid)
        self.assertEqual(fresh['state'], {})
        self.assertEqual(fresh['title'], '')
        self.assertEqual(fresh['session']['generation'], 2)
        self.assertEqual(self.new()['task_id'], fresh['task_id'])
        for operation in (lambda: self.store.read(tid),
                          lambda: self.store.patch(tid, 2, {'summary': 'stale'}),
                          lambda: self.store.checkpoint(tid, 2, {}),
                          lambda: self.store.patch(tid, 1, {'summary': 'Do not resurrect', 'decisions': ['Old decision']}, 'old-write'),
                          lambda: self.new('unassociated', task_id=tid)):
            with self.assertRaises(cs.DeletedTaskError):
                operation()
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.assertEqual(len(self.store.list_tasks(True)), 2)

    def test_missing_task_row_with_session_mapping_recreates_empty_context(self):
        old = self.new()
        # Simulate an externally removed row; our public deletion never breaks FKs.
        with sqlite3.connect(self.store.path) as conn:
            conn.execute('DELETE FROM tasks WHERE task_id=?', (old['task_id'],))
        fresh = self.new(task_id=old['task_id'])
        self.assertTrue(fresh['reset'])
        self.assertNotEqual(fresh['task_id'], old['task_id'])
        self.assertEqual(fresh['state'], {})
        self.assertEqual(fresh['session']['generation'], 2)
        self.assertEqual(self.new()['task_id'], fresh['task_id'])

    def test_explicit_restore_does_not_relink_continuing_session(self):
        tid = self.new()['task_id']
        self.store.patch(tid, 1, {'summary': 'Recover only explicitly'})
        self.store.delete(tid)
        fresh = self.new()
        restored = self.store.restore(tid)
        self.assertEqual(self.store.read(tid)['state']['summary'], 'Recover only explicitly')
        self.assertEqual(self.new()['task_id'], fresh['task_id'])
        self.assertGreater(restored['version'], 2)
        with self.assertRaises(cs.ConflictError):
            self.store.patch(tid, 2, {'summary': 'old writer'})

    def test_manual_archive_and_reopen_preserves_content(self):
        tid = self.new()['task_id']
        self.store.patch(tid, 1, {'summary': 'Keep me'})
        archived = self.store.archive(tid)
        self.assertEqual(archived['status'], 'archived')
        self.assertEqual(self.store.list_tasks(), [])
        reopened = self.new()
        self.assertEqual(reopened['task_id'], tid)
        self.assertEqual(reopened['state'], {'summary': 'Keep me'})
        self.assertFalse(reopened['reset'])
        self.assertGreater(reopened['version'], archived['version'])

    def test_session_lifecycle_is_manual_metadata_and_never_cleans_tasks(self):
        task = self.new()
        tid = task['task_id']
        self.new('two', task_id=tid)
        for sid in ('one', 'two'):
            result = self.store.session_state('codex', sid, 'archived', expected_task_id=tid)
            self.assertEqual(result['status'], 'active')
        self.assertEqual(self.store.read(tid)['status'], 'active')
        self.assertEqual(self.store.gc(True)['archived'], [])
        self.assertEqual(self.store.read(tid)['version'], 1)
        self.store.session_state('codex', 'two', 'deleted', expected_task_id=tid)
        self.assertEqual(self.store.read(tid)['status'], 'active')

    def test_unknown_session_status_preserved(self):
        tid = self.new()['task_id']
        self.store.session_state('codex', 'one', 'unknown', expected_task_id=tid)
        self.assertEqual(self.store.stats()['sessions']['unknown'], 1)
        self.assertEqual(self.store.gc()['permanently_deleted'], 0)
        self.assertEqual(self.store.gc(True)['permanently_deleted'], 0)
        self.assertEqual(self.store.read(tid)['status'], 'active')

    def test_late_lifecycle_observation_cannot_touch_fresh_task(self):
        old = self.new()
        tid = old['task_id']
        self.store.delete(tid)
        fresh = self.new()
        with self.assertRaises(cs.StaleAssociationError):
            self.store.session_state('codex', 'one', 'archived', expected_task_id=tid)
        with self.assertRaises(cs.StaleAssociationError):
            self.store.session_state('codex', 'one', 'deleted', expected_task_id=fresh['task_id'], expected_generation=1)
        with self.assertRaises(cs.StaleAssociationError):
            self.store.session_state('codex', 'one', 'archived')
        self.assertEqual(self.store.read(fresh['task_id'])['status'], 'active')

    def test_out_of_order_and_invalid_timestamps(self):
        tid = self.new()['task_id']
        newer = self.store.session_state('codex', 'one', 'archived', '2100-01-01T00:00:00Z', expected_task_id=tid)
        older = self.store.session_state('codex', 'one', 'active', '2099-01-01T00:00:00Z', expected_task_id=tid)
        equal = self.store.session_state('codex', 'one', 'active', '2100-01-01T00:00:00Z', expected_task_id=tid)
        self.assertFalse(newer['ignored'])
        self.assertTrue(older['ignored'])
        self.assertTrue(equal['ignored'])
        self.assertEqual(older['session_status'], 'archived')
        for invalid in ('bad', '2026-01-01', float('nan'), float('inf'), -1, True):
            with self.assertRaises(cs.ContextError):
                self.store.session_state('codex', 'one', 'unknown', invalid, expected_task_id=tid)

    def test_relink_generation_fences_returned_session(self):
        original = self.new()
        target = self.new('two')
        self.new(task_id=target['task_id'])
        returned = self.new(task_id=original['task_id'])
        self.assertEqual(returned['session']['generation'], 3)
        with self.assertRaises(cs.StaleAssociationError):
            self.store.session_state('codex', 'one', 'archived', expected_task_id=original['task_id'], expected_generation=1)

    def test_idempotent_write_and_misuse(self):
        tid = self.new()['task_id']
        first = self.store.patch(tid, 1, {'goal': 'One'}, 'request-1')
        self.store.patch(tid, 2, {'summary': 'Two'})
        replay = self.store.patch(tid, 1, {'goal': 'One'}, 'request-1')
        self.assertEqual(replay['version'], first['version'])
        self.assertEqual(replay['current_version'], 3)
        self.assertTrue(replay['replayed'])
        self.assertEqual(self.store.read(tid)['version'], 3)
        with self.assertRaises(cs.IdempotencyError):
            self.store.patch(tid, 1, {'goal': 'Wrong'}, 'request-1')
        with self.assertRaises(cs.IdempotencyError):
            self.store.checkpoint(tid, 1, {'goal': 'One'}, 'request-1')

    def test_finite_idempotency_receipts_and_deleted_key_versions(self):
        tid = self.new()['task_id']
        version = 1
        for i in range(cs.MAX_REQUESTS_PER_TASK + 7):
            version = self.store.patch(tid, version, {'progress': str(i)}, f'req-{i}')['version']
        with sqlite3.connect(self.store.path) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM requests').fetchone()[0], cs.MAX_REQUESTS_PER_TASK)
            self.assertEqual(len(json.loads(conn.execute('SELECT field_versions FROM tasks').fetchone()[0])), 1)
        with self.assertRaises(cs.ConflictError):
            self.store.patch(tid, 1, {'progress': '0'}, 'req-0')
        self.assertEqual(self.store.read(tid)['version'], version)

    def test_noop_writes_advance_versions_for_safe_retry(self):
        tid = self.new()['task_id']
        self.assertEqual(self.store.patch(tid, 1, {})['version'], 2)
        self.assertEqual(self.store.read(tid, 1)['changes'], {})

    def test_validation_bounds_schema_and_utf8(self):
        tid = self.new()['task_id']
        invalid = [[], {'transcript': 'never'}, {'summary': {'nested': 'no'}},
                   {'summary': 7}, {'summary': 'x' * 4097},
                   {'next': ['x'] * 65}, {'next': ['x' * 1025]},
                   {key: '中' * 4096 for key in ('summary', 'goal')}]
        for payload in invalid:
            with self.assertRaises(cs.ContextError):
                self.store.patch(tid, 1, payload)
        self.assertEqual(self.store.read(tid)['version'], 1)
        for invalid_version in (-1, '1', None, True):
            with self.assertRaises(cs.ContextError):
                self.store.patch(tid, invalid_version, {})
        for provider, sid in (('', 'one'), ('codex', ''), ('codex', 'a\nb'), ('x' * 65, 'one')):
            with self.assertRaises(cs.ContextError):
                self.new(sid, provider)

    def test_combined_state_limit_is_transactional(self):
        tid = self.new()['task_id']
        self.store.patch(tid, 1, {'summary': 'x' * 4096, 'goal': 'x' * 4096, 'progress': 'x' * 4096})
        with self.assertRaises(cs.ContextError):
            self.store.patch(tid, 2, {'evidence': 'x' * 4096})
        self.assertEqual(self.store.read(tid)['version'], 2)
        self.assertNotIn('evidence', self.store.read(tid)['state'])

    def test_restarts_retain_state_and_reset_fence(self):
        tid = self.new()['task_id']
        self.store.patch(tid, 1, {'goal': 'Persist'})
        restarted = cs.ContextStore(self.project, self.data)
        self.assertEqual(restarted.open_session('codex', 'one')['state'], {'goal': 'Persist'})
        restarted.delete(tid)
        fresh = cs.ContextStore(self.project, self.data).open_session('codex', 'one')
        self.assertTrue(fresh['reset'])
        self.assertEqual(fresh['state'], {})

    def test_concurrent_ensure_creates_one_record(self):
        def ensure(_):
            return cs.ContextStore(self.project, self.data).open_session('codex', 'same')['task_id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(ensure, range(24)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(self.store.stats()['total_tasks'], 1)

    def test_concurrent_fresh_reset_creates_one_new_record(self):
        tid = self.new()['task_id']
        self.store.delete(tid)
        def ensure(_):
            return cs.ContextStore(self.project, self.data).open_session('codex', 'one')['task_id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(ensure, range(16)))
        self.assertEqual(len(set(results)), 1)
        self.assertNotEqual(results[0], tid)
        self.assertEqual(self.store.stats()['total_tasks'], 2)

    def test_concurrent_cas_only_one_writer_wins(self):
        tid = self.new()['task_id']
        def write(index):
            try:
                return cs.ContextStore(self.project, self.data).patch(tid, 1, {'summary': str(index)})
            except cs.ConflictError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(write, range(16)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.store.read(tid)['version'], 2)

    def test_process_exit_rolls_back_uncommitted_write(self):
        tid = self.new()['task_id']
        script = "import sqlite3,sys,os; c=sqlite3.connect(sys.argv[1]); c.execute('BEGIN IMMEDIATE'); c.execute(\"UPDATE tasks SET state='{\\\"summary\\\":\\\"not committed\\\"}'\"); os._exit(0)"
        result = subprocess.run([sys.executable, '-c', script, str(self.store.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(cs.ContextStore(self.project, self.data).read(tid)['state'], {})

    def test_cross_project_task_ids_and_sessions_do_not_leak(self):
        tid = self.new()['task_id']
        self.store.patch(tid, 1, {'summary': 'Private to A'})
        other_path = self.root / 'other'; other_path.mkdir(); (other_path / '.git').mkdir()
        other = cs.ContextStore(other_path, self.data)
        for operation in (lambda: other.read(tid), lambda: other.patch(tid, 2, {}),
                          lambda: other.open_session('codex', 'one', task_id=tid),
                          lambda: other.delete(tid), lambda: other.restore(tid)):
            with self.assertRaises(cs.NotFoundError):
                operation()
        theirs = other.open_session('codex', 'one')
        self.assertNotEqual(theirs['task_id'], tid)
        self.assertEqual(theirs['state'], {})
        self.assertEqual(other.stats()['total_tasks'], 1)

    def test_git_worktrees_share_scope_subdirectories_too(self):
        def git(*args):
            r = subprocess.run(['git', '-C', str(self.project), *args], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r
        git('init')
        git('-c', 'user.email=test@example.com', '-c', 'user.name=Test', 'commit', '--allow-empty', '-m', 'fixture')
        worktree = self.root / 'linked'
        git('worktree', 'add', '--detach', str(worktree))
        nested = worktree / 'nested'; nested.mkdir()
        main = cs.ContextStore(self.project, self.data)
        secondary = cs.ContextStore(worktree, self.data)
        sub = cs.ContextStore(nested, self.data)
        self.assertEqual(main.scope_id, secondary.scope_id)
        self.assertEqual(main.scope_id, sub.scope_id)
        tid = main.open_session('codex', 'git-one')['task_id']
        main.patch(tid, 1, {'goal': 'Same repository'})
        self.assertEqual(secondary.read(tid)['state'], {'goal': 'Same repository'})
        with patch.dict(os.environ, {'GIT_DIR': '/bogus', 'GIT_WORK_TREE': '/bogus'}):
            self.assertEqual(cs.ContextStore(nested, self.data).scope_id, main.scope_id)

    def test_global_decisions_outlive_task_and_project_decisions_stay_scoped(self):
        tid = self.new()['task_id']
        self.store.decision_put('language', 'Chinese', scope='global')
        self.store.decision_put('project-rule', 'Only here')
        self.store.delete(tid)
        other_path = self.root / 'other'; other_path.mkdir(); (other_path / '.git').mkdir()
        other = cs.ContextStore(other_path, self.data)
        self.assertEqual(other.decision_list('global')[0]['value'], 'Chinese')
        self.assertEqual(other.decision_list(), [])
        self.assertEqual(self.store.decision_list()[0]['value'], 'Only here')
        self.store.gc(True)
        self.assertEqual(self.store.decision_list('global')[0]['value'], 'Chinese')

    def test_decision_cas_soft_delete_and_explicit_restore(self):
        created = self.store.decision_put('name', 'Initial')
        self.assertEqual(created['version'], 1)
        with self.assertRaises(cs.ConflictError):
            self.store.decision_put('name', 'Overwrite')
        updated = self.store.decision_put('name', 'New', expected_version=1)
        deleted = self.store.decision_delete('name', expected_version=updated['version'])
        self.assertEqual(self.store.decision_list(), [])
        self.assertNotIn('value', self.store.decision_list(include_deleted=True)[0])
        with self.assertRaises(cs.ConflictError):
            self.store.decision_put('name', 'Old write', expected_version=2)
        restored = self.store.decision_restore('name', expected_version=deleted['version'])
        self.assertEqual(restored['value'], 'New')
        self.assertEqual(restored['version'], 4)
        with self.assertRaises(cs.ContextError):
            self.store.decision_put('bad', 'Data', scope='other-project')

    def test_capacity_is_soft_and_gc_is_nondestructive(self):
        with patch.object(cs, 'SOFT_TASK_CAPACITY', 2):
            self.new()
            second = self.new('two')
            self.assertTrue(second['warnings'])
            self.new('three')
            self.assertEqual(self.store.stats()['total_tasks'], 3)
        self.store.delete(second['task_id'])
        before = self.store.list_tasks(True)
        self.store.gc(True)
        self.assertEqual(self.store.list_tasks(True), before)

    def test_environment_override_and_default_home(self):
        with patch.dict(os.environ, {'CREW_DATA_HOME': str(self.root / 'env-data')}):
            self.assertEqual(cs.ContextStore(self.project).data_home, self.root / 'env-data')
            self.assertEqual(cs.ContextStore(self.project, self.data).data_home, self.data)
        with patch.dict(os.environ, {'CODEX_HOME': str(self.root / 'custom-codex')}):
            with patch.dict(os.environ):
                os.environ.pop('CREW_DATA_HOME', None)
                self.assertEqual(cs.ContextStore(self.project).data_home, self.root / 'custom-codex' / 'crew')
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_future_or_corrupt_schema_rejected_without_replacement(self):
        with sqlite3.connect(self.store.path) as conn:
            conn.execute('PRAGMA user_version=999')
        with self.assertRaises(cs.ContextError):
            cs.ContextStore(self.project, self.data)
        with sqlite3.connect(self.store.path) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 999)
        corrupt = self.root / 'corrupt'; corrupt.mkdir()
        (corrupt / 'context.sqlite3').write_text('not sqlite')
        with self.assertRaises(cs.ContextError):
            cs.ContextStore(self.project, corrupt)
        self.assertEqual((corrupt / 'context.sqlite3').read_text(), 'not sqlite')


if __name__ == '__main__':
    unittest.main()
