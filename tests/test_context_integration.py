import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from crew.context_store import ContextStore
from crew import context_hooks as hooks

CLI = Path(__file__).resolve().parents[1] / 'bin' / 'crew'

class ContextIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir(); (self.project / '.git').mkdir()
        self.env = patch.dict(os.environ, {'CREW_HOST': 'codex', 'CODEX_HOME': str(self.root/'codex'), 'CREW_DATA_HOME': str(self.root/'data')}); self.env.start()
        self.store = ContextStore(self.project)
    def tearDown(self): self.env.stop(); self.tmp.cleanup()
    def event(self, kind='UserPromptSubmit', **kwargs):
        return {'hook_event_name': kind, 'session_id': 'parent', 'cwd': str(self.project), **kwargs}
    def test_deleted_then_next_turn_new_empty(self):
        before = self.store.open_session('codex', 'parent')
        self.store.patch(before['task_id'], before['version'], {'summary': 'REMOVED_SECRET_STATE'}, request_id='first')
        self.store.delete(before['task_id'])
        result = hooks.handle(self.event(turn_id='next', prompt='anything'), str(self.project))
        packet = result['hookSpecificOutput']['additionalContext']
        after = self.store.open_session('codex', 'parent')
        self.assertNotEqual(before['task_id'], after['task_id']); self.assertEqual(after['state'], {})
        self.assertIn('fresh empty record', packet); self.assertNotIn('REMOVED_SECRET_STATE', packet)
        with self.assertRaises(Exception): self.store.patch(before['task_id'], before['version'], {'summary': 'old'}, request_id='first')
    def test_repeat_hook_idempotent_and_no_prompt_storage(self):
        event = self.event(turn_id='same', prompt='DO_NOT_PERSIST_PROMPT')
        first = hooks.handle(event, str(self.project)); a = self.store.open_session('codex', 'parent')
        hooks.handle(event, str(self.project)); b = self.store.open_session('codex', 'parent')
        self.assertEqual(a['task_id'], b['task_id'])
        for p in (self.root/'data').rglob('*'):
            if p.is_file(): self.assertNotIn(b'DO_NOT_PERSIST_PROMPT', p.read_bytes())
    def test_sessionend_does_not_archive(self):
        before = self.store.open_session('codex', 'parent')
        hooks.handle(self.event('SessionEnd', reason='other'), str(self.project))
        self.assertEqual(self.store.open_session('codex', 'parent')['task_id'], before['task_id'])
    def test_subagent_shares_parent_explicitly(self):
        parent = self.store.open_session('codex', 'parent')
        hooks.handle(self.event('SubagentStart', agent_id='worker'), str(self.project))
        child = self.store.open_session('codex', 'parent/agent/worker')
        self.assertEqual(parent['task_id'], child['task_id'])
    def test_compaction_reinjects_bounded_orientation(self):
        task = self.store.open_session('codex','parent')
        self.store.patch(task['task_id'], task['version'], {'summary': 'checkpoint summary', 'evidence': ['evidence pointer']})
        result = hooks.handle(self.event('SessionStart',source='compact'),str(self.project))
        self.assertIn('checkpoint summary', result['hookSpecificOutput']['additionalContext'])
        self.assertIn('omitted_fields',result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(hooks.handle(self.event('PostCompact'),str(self.project)),{})
    def test_missing_identity_does_not_create(self):
        result = hooks.handle({'hook_event_name':'UserPromptSubmit'},str(self.project))
        self.assertIn('identity unavailable',result['hookSpecificOutput']['additionalContext'])
    def test_claude_cli_manual_flow(self):
        result = subprocess.run([str(CLI),'--host','claude','context','open','--provider','claude','--session','manual-one','--project',str(self.project),'--lang','zh'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        opened=json.loads(result.stdout)
        r=subprocess.run([str(CLI),'--host','claude','context','read','--task',opened['task_id'],'--project',str(self.project)],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(json.loads(r.stdout)['state'],{})

    def test_corrupt_store_does_not_block_prompt(self):
        self.store.path.write_bytes(b'not a sqlite database')
        result = subprocess.run([str(CLI),'hook','receive','--project',str(self.project)],input=json.dumps(self.event()),capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('unavailable',result.stdout)
    def test_new_cli_conflict_localized(self):
        task=self.store.open_session('claude','one')
        self.store.patch(task['task_id'],task['version'],{'summary':'current'})
        payload=self.root/'patch.json';payload.write_text('{"summary":"stale"}')
        r=subprocess.run([str(CLI),'context','patch','--task',task['task_id'],'--expected-version',str(task['version']),'--file',str(payload),'--project',str(self.project),'--lang','zh'],capture_output=True,text=True)
        self.assertEqual(r.returncode,2)
        data=json.loads(r.stdout);self.assertIn('版本冲突',data['message']);self.assertEqual(data['error_type'],'ConflictError')
    def test_context_command_list_no_automatic_gc(self):
        r=subprocess.run([str(CLI),'context','--help'],capture_output=True,text=True)
        self.assertNotIn(',gc',r.stdout);self.assertIn('decision-restore',r.stdout)

    def test_deleted_decision_metadata_cli(self):
        self.store.decision_put('choice','deleted body',expected_version=0)
        self.store.decision_delete('choice',expected_version=1)
        r=subprocess.run([str(CLI),'context','decisions','--include-deleted','--project',str(self.project)],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout)
        row=json.loads(r.stdout)[0];self.assertEqual(row['key'],'choice');self.assertEqual(row['version'],2);self.assertNotIn('value',row)
    def test_long_child_identity_hash(self):
        import hashlib
        sid='s'*256;agent='a'*128
        result=hooks.handle({'hook_event_name':'SubagentStart','session_id':sid,'agent_id':agent},str(self.project))
        self.assertNotIn('unavailable',result['hookSpecificOutput']['additionalContext'])
        child_key='agent:'+hashlib.sha256(json.dumps([sid,agent]).encode()).hexdigest()
        self.assertEqual(self.store.open_session('codex',sid)['task_id'],self.store.open_session('codex',child_key)['task_id'])

    def test_existing_role_config_preserved_on_context_upgrade(self):
        from crew import core
        core.role_write('project',self.project,'reviewer',dict(core.presets()['reviewer'],model='user-model',effort='low'),'create')
        config=core.path('project',self.project);before=config.read_bytes()
        hooks.handle(self.event(),str(self.project))
        self.assertEqual(config.read_bytes(),before)
