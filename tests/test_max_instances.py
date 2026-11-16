"""Tests for per-role max_instances config and parallel subagent dispatch."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from crew import core, hosts, i18n, wizard, workflow_hooks
from crew.workflow import WorkflowStore
from crew.workflow_cli import present, report

CLI = Path(__file__).resolve().parents[1] / 'bin' / 'crew'


class MaxInstancesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / '.git').mkdir()
        self.env_dict = {
            'CODEX_HOME': str(self.root / 'codex'),
            'CLAUDE_CONFIG_DIR': str(self.root / 'claude'),
            'CREW_DATA_HOME': str(self.root / 'data'),
            'CREW_HOST': 'codex',
            'CREW_LANG': 'en',
        }
        self.env = patch.dict(os.environ, self.env_dict)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.before_lang = i18n.LANG
        self.addCleanup(lambda: i18n.set_language(self.before_lang))
        hosts.select('codex')
        i18n.set_language('en')

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), *args, '--project', str(self.project)],
            capture_output=True, text=True, env={**os.environ, **self.env_dict},
        )

    def test_config_persists_default_one_and_validates_bounds(self):
        r = self.run_cli('role', 'create', 'implementer', '--model', 'gpt-6.1-sol', '--effort', 'medium', '--description', 'Impl', '--instructions', 'Do work')
        self.assertEqual(r.returncode, 0, r.stderr)
        stored = json.loads(core.path('project', self.project).read_text())
        self.assertEqual(stored['roles']['implementer']['max_instances'], 1)
        self.assertEqual(core.preflight(self.project)['max_instances'], {'implementer': 1})

        # Update max_instances and verify unrelated update preserves it.
        r2 = self.run_cli('role', 'update', 'implementer', '--max-instances', '3')
        self.assertEqual(r2.returncode, 0, r2.stderr)
        r3 = self.run_cli('role', 'update', 'implementer', '--effort', 'high')
        self.assertEqual(r3.returncode, 0, r3.stderr)
        eff = core.effective(self.project)['roles']['implementer']
        self.assertEqual(eff['max_instances'], 3)
        self.assertEqual(eff['effort'], 'high')

        # Presets install writes explicit max_instances (default 1, or custom).
        r4 = self.run_cli('presets', 'install', 'reviewer', '--model', 'gpt-6-astra', '--effort', 'high')
        self.assertEqual(r4.returncode, 0, r4.stderr)
        r5 = self.run_cli('presets', 'install', 'tester', '--model', 'gpt-6.1-terra', '--effort', 'low', '--max-instances', '4')
        self.assertEqual(r5.returncode, 0, r5.stderr)
        self.assertEqual(core.preflight(self.project)['max_instances'], {'implementer': 3, 'reviewer': 1, 'tester': 4})

        # Exporting succeeds without drift caused by max_instances.
        core.export_agents(self.project, 'project', apply=True)
        self.assertEqual(core.preflight(self.project)['status'], 'ready')

        # Invalid max_instances are rejected.
        base = dict(core.presets('en')['researcher'], model='m', effort='low')
        for bad in (0, -2, True, False, '2', core.MAX_INSTANCES_LIMIT + 1):
            with self.subTest(bad=bad), self.assertRaises(core.Error):
                core.role_write('project', self.project, 'researcher', dict(base, max_instances=bad), 'create')

    def test_legacy_config_without_field_and_wizard_default_to_one(self):
        # Legacy config without max_instances normalizes to 1.
        core.role_write('project', self.project, 'backend', dict(core.presets('en')['implementer'], model='m', effort='low'), 'create')
        raw = json.loads(core.path('project', self.project).read_text())
        self.assertNotIn('max_instances', raw['roles']['backend'])
        self.assertEqual(core.effective(self.project)['roles']['backend']['max_instances'], 1)
        self.assertEqual(core.preflight(self.project)['max_instances']['backend'], 1)

        # Wizard writes explicit max_instances: 1.
        s = wizard.advance('project', self.project)
        s = wizard.advance('project', self.project, ['reviewer'], s['step']['id'], s['revision'])
        s = wizard.advance('project', self.project, 'gpt-6-astra', s['step']['id'], s['revision'])
        s = wizard.advance('project', self.project, 'high', s['step']['id'], s['revision'])
        s = wizard.advance('project', self.project, True, 'confirm', s['revision'], apply=True, token=s['step']['preview']['confirmation_token'])
        self.assertEqual(s['status'], 'complete')
        stored = json.loads(core.path('project', self.project).read_text())
        self.assertEqual(stored['roles']['reviewer']['max_instances'], 1)

    def test_parallel_workflow_dispatch_hooks_deliver_and_review_independence(self):
        core.role_write('project', self.project, 'implementer', dict(core.presets('en')['implementer'], model='gpt-6.1-sol', effort='medium', max_instances=3), 'create')
        core.role_write('project', self.project, 'reviewer', dict(core.presets('en')['reviewer'], model='gpt-6-astra', effort='high'), 'create')
        (self.project / 'a.py').write_text('a\n')
        (self.project / 'b.py').write_text('b\n')

        store = WorkflowStore(self.project)
        task = store.context.open_session('codex', 'sess-p')['task_id']
        d = store.start(task, 'sess-p', 'Parallel task', 'implementer', 'reviewer')

        # Exceeding max_instances fails cleanly.
        with self.assertRaisesRegex(core.Error, 'max_instances=3'):
            store.change(task, d['version'], 'dispatch', count=4)
        with self.assertRaisesRegex(core.Error, 'positive integer'):
            store.change(task, d['version'], 'dispatch', count=0)

        # Dispatch 2 parallel implementers (in zh so frozen slot display_name uses zh).
        i18n.set_language('zh')
        d = store.change(task, d['version'], 'dispatch', count=2)
        zh_p = present(d)
        self.assertEqual(len(zh_p['dispatch_inputs']), 2)
        self.assertEqual([x['name'] for x in zh_p['dispatch_inputs']], ['【gpt-6.1-sol】实施者#1', '【gpt-6.1-sol】实施者#2'])
        self.assertEqual([x['task_name'] for x in zh_p['dispatch_inputs']], ['gpt_6_1_sol_implementer_1', 'gpt_6_1_sol_implementer_2'])
        self.assertTrue(all(x['agent_type'] == 'crew_implementer' for x in zh_p['dispatch_inputs']))
        self.assertIn('子智能体命名：【gpt-6.1-sol】实施者#2\n', zh_p['dispatch_inputs'][1]['message'])

        # Spawn worker 1 and start it BEFORE spawning worker 2 (interleaved).
        kw1 = dict(tool_name='spawn_agent', tool_use_id='call-1',
                   tool_input={'agent_type': 'crew_implementer', 'task_name': zh_p['dispatch_inputs'][0]['task_name'],
                               'message': zh_p['dispatch_inputs'][0]['message']})
        kw2 = dict(tool_name='spawn_agent', tool_use_id='call-2',
                   tool_input={'agent_type': 'crew_implementer', 'task_name': zh_p['dispatch_inputs'][1]['task_name'],
                               'message': zh_p['dispatch_inputs'][1]['message']})
        self.assertEqual(workflow_hooks.handle(dict(hook_event_name='PreToolUse', session_id='sess-p', **kw1), self.project).get('hookSpecificOutput', {}).get('permissionDecision'), None)
        workflow_hooks.handle(dict(hook_event_name='PostToolUse', session_id='sess-p', tool_response={'agent_id': 'w1'}, **kw1), self.project)
        sub1 = workflow_hooks.handle(dict(hook_event_name='SubagentStart', session_id='sess-p', agent_id='w1', agent_type='crew_implementer'), self.project)
        self.assertIn('【gpt-6.1-sol】实施者#1', sub1['hookSpecificOutput']['additionalContext'])

        # Phase is now 'implementing'; spawning slot 2 must still be allowed!
        self.assertEqual(store.read(task)['phase'], 'implementing')
        self.assertEqual(workflow_hooks.handle(dict(hook_event_name='PreToolUse', session_id='sess-p', **kw2), self.project).get('hookSpecificOutput', {}).get('permissionDecision'), None)
        workflow_hooks.handle(dict(hook_event_name='PostToolUse', session_id='sess-p', tool_response={'agent_id': 'w2'}, **kw2), self.project)
        sub2 = workflow_hooks.handle(dict(hook_event_name='SubagentStart', session_id='sess-p', agent_id='w2', agent_type='crew_implementer'), self.project)
        self.assertIn('【gpt-6.1-sol】实施者#2', sub2['hookSpecificOutput']['additionalContext'])

        # Both w1 and w2 may edit; unknown agent or root is denied.
        for writer in ('w1', 'w2'):
            self.assertEqual(workflow_hooks.handle(dict(hook_event_name='PreToolUse', session_id='sess-p', tool_name='apply_patch', agent_id=writer, agent_type='crew_implementer'), self.project), {})
        self.assertEqual(workflow_hooks.handle(dict(hook_event_name='PreToolUse', session_id='sess-p', tool_name='apply_patch', agent_id='w3', agent_type='crew_implementer'), self.project)['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual(workflow_hooks.handle(dict(hook_event_name='PreToolUse', session_id='sess-p', tool_name='apply_patch'), self.project)['hookSpecificOutput']['permissionDecision'], 'deny')

        # Stopping only w1 still blocks deliver until w2 also stops.
        workflow_hooks.handle(dict(hook_event_name='SubagentStop', session_id='sess-p', agent_id='w1', agent_type='crew_implementer'), self.project)
        d = store.read(task)
        with self.assertRaises(core.Error):
            store.change(task, d['version'], 'deliver', files=['a.py', 'b.py'], summary='partial', tests='ok')
        # Stopped w1 can no longer edit while w2 still can.
        self.assertEqual(workflow_hooks.handle(dict(hook_event_name='PreToolUse', session_id='sess-p', tool_name='apply_patch', agent_id='w1', agent_type='crew_implementer'), self.project)['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual(workflow_hooks.handle(dict(hook_event_name='PreToolUse', session_id='sess-p', tool_name='apply_patch', agent_id='w2', agent_type='crew_implementer'), self.project), {})

        workflow_hooks.handle(dict(hook_event_name='SubagentStop', session_id='sess-p', agent_id='w2', agent_type='crew_implementer'), self.project)
        d = store.read(task)
        rep = report(d)
        self.assertEqual([a['agent_id'] for a in rep['assignments']], ['w1', 'w2'])
        self.assertEqual([a['subagent_name'] for a in rep['assignments']], ['【gpt-6.1-sol】实施者#1', '【gpt-6.1-sol】实施者#2'])

        d = store.change(task, d['version'], 'deliver', files=['a.py', 'b.py'], summary='both parts done', tests='all passed')
        self.assertEqual(d['delivery']['agent_ids'], ['w1', 'w2'])

        # Dispatch reviewer (max_instances=1 rejects count=2) and verify neither w1 nor w2 can review.
        with self.assertRaisesRegex(core.Error, 'max_instances=1'):
            store.change(task, d['version'], 'dispatch', count=2)
        d = store.change(task, d['version'], 'dispatch')
        kwr = dict(tool_name='spawn_agent', tool_use_id='call-r',
                   tool_input={'agent_type': 'crew_reviewer', 'message': present(d)['dispatch_input']['message']})
        workflow_hooks.handle(dict(hook_event_name='PreToolUse', session_id='sess-p', **kwr), self.project)
        workflow_hooks.handle(dict(hook_event_name='PostToolUse', session_id='sess-p', tool_response={'agent_id': 'w2'}, **kwr), self.project)
        self.assertIsNone(store.read(task)['attempt']['agent_id'])

    def test_cli_workflow_dispatch_count(self):
        core.role_write('project', self.project, 'implementer', dict(core.presets('en')['implementer'], model='gpt-6.1-sol', effort='low', max_instances=2), 'create')
        core.role_write('project', self.project, 'reviewer', dict(core.presets('en')['reviewer'], model='gpt-6-astra', effort='high'), 'create')
        open_out = json.loads(self.run_cli('context', 'open', '--session', 'cli-s').stdout)
        start_out = json.loads(self.run_cli('workflow', 'start', '--session', 'cli-s', '--task', open_out['task_id'], '--goal', 'g', '--acceptance', 'a', '--implementer', 'implementer', '--reviewer', 'reviewer').stdout)
        disp = self.run_cli('workflow', 'dispatch', '--task', open_out['task_id'], '--expected-version', str(start_out['version']), '--count', '2')
        self.assertEqual(disp.returncode, 0, disp.stderr + disp.stdout)
        payload = json.loads(disp.stdout)
        self.assertEqual(len(payload['dispatch_inputs']), 2)
        bad = self.run_cli('workflow', 'dispatch', '--task', open_out['task_id'], '--expected-version', str(payload['version']), '--count', '2')
        self.assertEqual(bad.returncode, 2)

    def test_coordinator_prompts_mention_max_instances(self):
        for host in ('codex', 'claude'):
            hosts.select(host)
            self.assertIn('max_instances', core.presets('zh')['coordinator']['instructions'])
            self.assertIn('max_instances', core.presets('en')['coordinator']['instructions'])
        hosts.select('codex')


if __name__ == '__main__':
    unittest.main()
