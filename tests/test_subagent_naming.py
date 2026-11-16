"""Tests for canonical [model]role subagent naming across hosts, workflows, and hooks."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crew import cli, context_hooks, core, hosts, i18n, workflow_hooks
from crew.workflow import WorkflowStore
from crew.workflow_cli import present, report


class SubagentNamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / '.git').mkdir()
        self.env = patch.dict(os.environ, {
            'CODEX_HOME': str(self.root / 'codex'),
            'CLAUDE_CONFIG_DIR': str(self.root / 'claude'),
            'CREW_DATA_HOME': str(self.root / 'data'),
            'CREW_HOST': 'codex',
            'CREW_LANG': 'en',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.before_lang = i18n.LANG
        self.addCleanup(lambda: i18n.set_language(self.before_lang))
        hosts.select('codex')
        i18n.set_language('en')

    def test_role_label_and_subagent_name_localize_builtin_and_keep_custom(self):
        expected_zh = {
            'coordinator': '协调者',
            'implementer': '实施者',
            'reviewer': '审查者',
            'researcher': '研究者',
            'tester': '测试者',
        }
        for role_id, zh_name in expected_zh.items():
            self.assertEqual(hosts.subagent_name(role_id, 'gpt-6.1-sol', 'en'), f'[gpt-6.1-sol]{role_id}')
            self.assertEqual(hosts.subagent_name(role_id, 'gpt-6.1-sol', 'zh'), f'[gpt-6.1-sol]{zh_name}')
        self.assertEqual(hosts.subagent_name('backend', 'gpt-6.1-sol', 'en'), '[gpt-6.1-sol]backend')
        self.assertEqual(hosts.subagent_name('backend', 'gpt-6.1-sol', 'zh'), '[gpt-6.1-sol]backend')

    def test_preflight_reports_localized_subagent_names_for_active_roles(self):
        core.role_write('project', self.project, 'implementer', dict(core.presets('en')['implementer'], model='gpt-6.1-sol', effort='medium'), 'create')
        core.role_write('project', self.project, 'backend', dict(core.presets('en')['implementer'], model='gpt-6.1-terra', effort='low'), 'create')
        core.role_write('project', self.project, 'reviewer', dict(core.presets('en')['reviewer'], model='gpt-6-astra', effort='high', disabled=True), 'create')

        i18n.set_language('en')
        en_state = core.preflight(self.project)
        self.assertEqual(en_state['subagent_names'], {
            'implementer': '[gpt-6.1-sol]implementer',
            'backend': '[gpt-6.1-terra]backend',
        })

        i18n.set_language('zh')
        zh_state = core.preflight(self.project)
        self.assertEqual(zh_state['subagent_names'], {
            'implementer': '[gpt-6.1-sol]实施者',
            'backend': '[gpt-6.1-terra]backend',
        })

    def test_dispatch_report_and_hooks_use_canonical_subagent_names(self):
        core.role_write('project', self.project, 'implementer', dict(core.presets('en')['implementer'], model='gpt-6.1-sol', effort='low'), 'create')
        core.role_write('project', self.project, 'reviewer', dict(core.presets('en')['reviewer'], model='gpt-6-astra', effort='high'), 'create')
        (self.project / 'app.py').write_text('ok\n')
        store = WorkflowStore(self.project)
        task = store.context.open_session('codex', 'sess-1')['task_id']
        d = store.start(task, 'sess-1', 'Ship feature', 'implementer', 'reviewer')

        # English dispatch & report
        i18n.set_language('en')
        d = store.change(task, d['version'], 'dispatch')
        en_packet = present(d)
        self.assertEqual(en_packet['dispatch_input']['agent_type'], 'crew_implementer')
        self.assertEqual(en_packet['dispatch_input']['name'], '[gpt-6.1-sol]implementer')
        self.assertTrue(en_packet['dispatch_input']['message'].startswith(f"[crew-dispatch:{d['attempt']['token']}]\n"))
        self.assertIn('Subagent name: [gpt-6.1-sol]implementer\n', en_packet['dispatch_input']['message'])
        self.assertIn('[gpt-6.1-sol]implementer', en_packet['next'])

        # Chinese dispatch view on the same state
        i18n.set_language('zh')
        zh_packet = present(d)
        self.assertEqual(zh_packet['dispatch_input']['agent_type'], 'crew_implementer')
        self.assertEqual(zh_packet['dispatch_input']['name'], '[gpt-6.1-sol]实施者')
        self.assertIn('子智能体命名：[gpt-6.1-sol]实施者\n', zh_packet['dispatch_input']['message'])
        self.assertIn('（当前指派：[gpt-6.1-sol]实施者）', zh_packet['next'])

        # Hook SubagentStart delivers canonical name
        kw = dict(tool_name='spawn_agent', tool_use_id='call-1',
                  tool_input={'agent_type': 'crew_implementer', 'message': en_packet['dispatch_input']['message']})
        workflow_hooks.handle(dict(hook_event_name='PreToolUse', session_id='sess-1', **kw), self.project)
        workflow_hooks.handle(dict(hook_event_name='PostToolUse', session_id='sess-1', tool_response={'agent_id': 'w1'}, **kw), self.project)
        sub_out = workflow_hooks.handle(dict(hook_event_name='SubagentStart', session_id='sess-1', agent_id='w1', agent_type='crew_implementer'), self.project)
        self.assertIn('[gpt-6.1-sol]实施者', sub_out['hookSpecificOutput']['additionalContext'])
        workflow_hooks.handle(dict(hook_event_name='SubagentStop', session_id='sess-1', agent_id='w1', agent_type='crew_implementer'), self.project)

        # Deliver, review, and check report in both languages
        d = store.read(task)
        d = store.change(task, d['version'], 'deliver', files=['app.py'], summary='done', tests='ok')
        d = store.change(task, d['version'], 'dispatch')
        kw2 = dict(tool_name='spawn_agent', tool_use_id='call-2',
                   tool_input={'agent_type': 'crew_reviewer', 'message': present(d)['dispatch_input']['message']})
        workflow_hooks.handle(dict(hook_event_name='PreToolUse', session_id='sess-1', **kw2), self.project)
        workflow_hooks.handle(dict(hook_event_name='PostToolUse', session_id='sess-1', tool_response={'agent_id': 'r1'}, **kw2), self.project)
        workflow_hooks.handle(dict(hook_event_name='SubagentStart', session_id='sess-1', agent_id='r1', agent_type='crew_reviewer'), self.project)
        workflow_hooks.handle(dict(hook_event_name='SubagentStop', session_id='sess-1', agent_id='r1', agent_type='crew_reviewer'), self.project)
        d = store.read(task)
        d = store.change(task, d['version'], 'review', verdict='pass', summary='good')

        i18n.set_language('en')
        en_report = report(d)
        self.assertEqual(en_report['delivery']['subagent_name'], '[gpt-6.1-sol]implementer')
        self.assertEqual(en_report['review']['subagent_name'], '[gpt-6-astra]reviewer')

        i18n.set_language('zh')
        zh_report = report(d)
        self.assertEqual(zh_report['delivery']['subagent_name'], '[gpt-6.1-sol]实施者')
        self.assertEqual(zh_report['review']['subagent_name'], '[gpt-6-astra]审查者')

    def test_claude_context_hook_subagent_start_includes_canonical_name(self):
        hosts.select('claude')
        try:
            core.role_write('project', self.project, 'reviewer', dict(core.presets('en')['reviewer'], model='sonnet', effort='high'), 'create')
            i18n.set_language('zh')
            out = context_hooks.handle({
                'hook_event_name': 'SubagentStart',
                'session_id': 'claude-sess',
                'agent_id': 'a1',
                'agent_type': 'crew-reviewer',
            }, self.project)
            ctx = out['hookSpecificOutput']['additionalContext']
            self.assertIn('"subagent_name":"[sonnet]审查者"', ctx)

            i18n.set_language('en')
            out_en = context_hooks.handle({
                'hook_event_name': 'SubagentStart',
                'session_id': 'claude-sess',
                'agent_id': 'a2',
                'agent_type': 'crew-reviewer',
            }, self.project)
            self.assertIn('"subagent_name":"[sonnet]reviewer"', out_en['hookSpecificOutput']['additionalContext'])
        finally:
            hosts.select('codex')

    def test_coordinator_presets_include_naming_rule(self):
        for host in ('codex', 'claude'):
            hosts.select(host)
            self.assertIn('[模型名]角色名', core.presets('zh')['coordinator']['instructions'])
            self.assertIn('[model]role', core.presets('en')['coordinator']['instructions'])
        hosts.select('codex')


if __name__ == '__main__':
    unittest.main()
