"""Tests for canonical 【model】role subagent naming, Codex ASCII constraints, and hard dispatch gates."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from crew import context_hooks, core, hosts, i18n, workflow_hooks
from crew.workflow import WorkflowStore
from crew.workflow_cli import present, report

CLI = Path(__file__).resolve().parents[1] / 'bin' / 'crew'


class SubagentNamingTests(unittest.TestCase):
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

    def test_role_label_and_subagent_name_localize_builtin_and_keep_custom(self):
        expected_zh = {
            'coordinator': '协调者',
            'implementer': '实施者',
            'reviewer': '审查者',
            'researcher': '研究者',
            'tester': '测试者',
            'documenter': '文档记录员',
        }
        for role_id, zh_name in expected_zh.items():
            self.assertEqual(hosts.subagent_name(role_id, 'gpt-6.1-sol', 'en'), f'【gpt-6.1-sol】{role_id}')
            self.assertEqual(hosts.subagent_name(role_id, 'gpt-6.1-sol', 'zh'), f'【gpt-6.1-sol】{zh_name}')
        self.assertEqual(hosts.subagent_name('backend', 'gpt-6.1-sol', 'en'), '【gpt-6.1-sol】backend')
        self.assertEqual(hosts.subagent_name('backend', 'gpt-6.1-sol', 'zh'), '【gpt-6.1-sol】backend')

    def test_codex_task_name_and_native_nicknames_match_codex_constraints(self):
        self.assertEqual(hosts.task_name('implementer', 'gpt-6.1-sol'), 'gpt_6_1_sol_implementer')
        self.assertEqual(hosts.task_name('implementer', 'gpt-6.1-sol', index=2, seq=3), 'gpt_6_1_sol_implementer_2_r3')
        self.assertRegex(hosts.task_name('qa-lead', 'OpenAI/GPT-6.1'), r'^[a-z0-9_]+$')

        nicks = hosts.native_nicknames('implementer', 'gpt-6.1-sol', 1)
        self.assertEqual(nicks, ['gpt-6-1-sol implementer'])
        self.assertTrue(all(hosts.NICKNAME_RULE.match(n) for n in nicks))

        multi = hosts.native_nicknames('implementer', 'gpt-6.1-sol', 3)
        self.assertEqual(multi, ['gpt-6-1-sol implementer 1', 'gpt-6-1-sol implementer 2', 'gpt-6-1-sol implementer 3'])
        self.assertTrue(all(hosts.NICKNAME_RULE.match(n) for n in multi))
        self.assertEqual(hosts.native_nicknames('implementer', '中文模型', 1), [])

    def test_codex_export_writes_ascii_nickname_candidates_and_detects_drift(self):
        core.role_write('project', self.project, 'implementer',
                        dict(core.presets('en')['implementer'], model='gpt-6.1-sol', effort='medium', max_instances=2), 'create')
        core.export_agents(self.project, 'project', apply=True)
        toml_path = self.project / '.codex' / 'agents' / 'crew-implementer.toml'
        parsed = tomllib.loads(toml_path.read_text())
        self.assertEqual(parsed['nickname_candidates'], ['gpt-6-1-sol implementer 1', 'gpt-6-1-sol implementer 2'])
        self.assertEqual(core.preflight(self.project)['export_drift'], [])

        # Missing or altered nickname_candidates triggers export drift.
        without_nicks = '\n'.join(l for l in toml_path.read_text().splitlines() if not l.startswith('nickname_candidates')) + '\n'
        toml_path.write_text(without_nicks)
        self.assertEqual(core.preflight(self.project)['export_drift'], ['implementer'])

    def test_preflight_and_role_spawn_return_fixed_variables(self):
        core.role_write('project', self.project, 'implementer', dict(core.presets('en')['implementer'], model='gpt-6.1-sol', effort='medium', max_instances=2), 'create')
        core.role_write('project', self.project, 'backend', dict(core.presets('en')['implementer'], model='gpt-6.1-terra', effort='low'), 'create')
        core.role_write('project', self.project, 'reviewer', dict(core.presets('en')['reviewer'], model='gpt-6-astra', effort='high', disabled=True), 'create')

        i18n.set_language('en')
        en_state = core.preflight(self.project)
        self.assertEqual(en_state['subagent_names'], {
            'implementer': '【gpt-6.1-sol】implementer',
            'backend': '【gpt-6.1-terra】backend',
        })
        self.assertEqual(en_state['task_names'], {
            'implementer': 'gpt_6_1_sol_implementer',
            'backend': 'gpt_6_1_terra_backend',
        })

        i18n.set_language('zh')
        zh_state = core.preflight(self.project)
        self.assertEqual(zh_state['subagent_names'], {
            'implementer': '【gpt-6.1-sol】实施者',
            'backend': '【gpt-6.1-terra】backend',
        })

        out = json.loads(self.run_cli('--lang', 'zh', 'role', 'spawn', 'implementer', '--count', '2').stdout)
        self.assertEqual([i['name'] for i in out['instances']], ['【gpt-6.1-sol】实施者#1', '【gpt-6.1-sol】实施者#2'])
        self.assertEqual([i['task_name'] for i in out['instances']], ['gpt_6_1_sol_implementer_1', 'gpt_6_1_sol_implementer_2'])
        self.assertEqual(out['native_nickname_candidates'], ['gpt-6-1-sol implementer 1', 'gpt-6-1-sol implementer 2'])
        bad = self.run_cli('role', 'spawn', 'implementer', '--count', '3')
        self.assertEqual(bad.returncode, 2)

    def test_hard_spawn_gate_blocks_improvised_or_undispatched_names(self):
        core.role_write('project', self.project, 'implementer', dict(core.presets('en')['implementer'], model='gpt-6.1-sol', effort='low'), 'create')
        core.role_write('project', self.project, 'reviewer', dict(core.presets('en')['reviewer'], model='gpt-6-astra', effort='high'), 'create')
        store = WorkflowStore(self.project)
        task = store.context.open_session('codex', 'sess-gate')['task_id']
        d = store.start(task, 'sess-gate', 'Ship feature', 'implementer', 'reviewer')

        # 1. Cannot spawn a managed workflow role before workflow dispatch, even without marker.
        denied_no_dispatch = workflow_hooks.handle({
            'hook_event_name': 'PreToolUse',
            'session_id': 'sess-gate',
            'tool_name': 'spawn_agent',
            'tool_use_id': 'call-0',
            'tool_input': {'agent_type': 'crew_implementer', 'task_name': 'implementer', 'message': 'go implement'},
        }, self.project)
        self.assertEqual(denied_no_dispatch['hookSpecificOutput']['permissionDecision'], 'deny')

        # 2. Dispatch fixes task_name & display_name once; improvised task_name or missing marker is blocked.
        d = store.change(task, d['version'], 'dispatch')
        pkt = present(d)['dispatch_input']
        self.assertEqual(pkt['task_name'], 'gpt_6_1_sol_implementer')

        denied_bad_task_name = workflow_hooks.handle({
            'hook_event_name': 'PreToolUse',
            'session_id': 'sess-gate',
            'tool_name': 'spawn_agent',
            'tool_use_id': 'call-1',
            'tool_input': {'agent_type': 'crew_implementer', 'task_name': 'implementer', 'message': pkt['message']},
        }, self.project)
        self.assertEqual(denied_bad_task_name['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertIn('gpt_6_1_sol_implementer', denied_bad_task_name['hookSpecificOutput']['permissionDecisionReason'])

        denied_no_marker = workflow_hooks.handle({
            'hook_event_name': 'PreToolUse',
            'session_id': 'sess-gate',
            'tool_name': 'spawn_agent',
            'tool_use_id': 'call-1',
            'tool_input': {'agent_type': 'crew_implementer', 'task_name': 'gpt_6_1_sol_implementer', 'message': 'no marker'},
        }, self.project)
        self.assertEqual(denied_no_marker['hookSpecificOutput']['permissionDecision'], 'deny')

        # 3. Exact dispatch_input is allowed; non-workflow helper role remains unrestricted.
        allowed = workflow_hooks.handle({
            'hook_event_name': 'PreToolUse',
            'session_id': 'sess-gate',
            'tool_name': 'spawn_agent',
            'tool_use_id': 'call-1',
            'tool_input': {'agent_type': 'crew_implementer', 'task_name': pkt['task_name'], 'message': pkt['message']},
        }, self.project)
        self.assertNotIn('permissionDecision', allowed.get('hookSpecificOutput', {}))

        other_role = workflow_hooks.handle({
            'hook_event_name': 'PreToolUse',
            'session_id': 'sess-gate',
            'tool_name': 'spawn_agent',
            'tool_use_id': 'call-x',
            'tool_input': {'agent_type': 'explorer', 'task_name': 'scan', 'message': 'read docs'},
        }, self.project)
        self.assertNotIn('permissionDecision', other_role.get('hookSpecificOutput', {}))

    def test_dispatch_report_and_hooks_use_canonical_subagent_names(self):
        core.role_write('project', self.project, 'implementer', dict(core.presets('en')['implementer'], model='gpt-6.1-sol', effort='low'), 'create')
        core.role_write('project', self.project, 'reviewer', dict(core.presets('en')['reviewer'], model='gpt-6-astra', effort='high'), 'create')
        (self.project / 'app.py').write_text('ok\n')
        store = WorkflowStore(self.project)
        task = store.context.open_session('codex', 'sess-1')['task_id']
        d = store.start(task, 'sess-1', 'Ship feature', 'implementer', 'reviewer')

        # English dispatch & stored display_name
        i18n.set_language('en')
        d = store.change(task, d['version'], 'dispatch')
        en_packet = present(d)
        self.assertEqual(en_packet['dispatch_input']['agent_type'], 'crew_implementer')
        self.assertEqual(en_packet['dispatch_input']['task_name'], 'gpt_6_1_sol_implementer')
        self.assertEqual(en_packet['dispatch_input']['name'], '【gpt-6.1-sol】implementer')
        self.assertEqual(en_packet['dispatch_input']['native_nickname_candidates'], ['gpt-6-1-sol implementer'])
        self.assertTrue(en_packet['dispatch_input']['message'].startswith(f"[crew-dispatch:{d['attempt']['token']}]\n"))
        self.assertIn('Subagent name: 【gpt-6.1-sol】implementer\n', en_packet['dispatch_input']['message'])
        self.assertIn('【gpt-6.1-sol】implementer', en_packet['next'])

        # Stored display_name stays frozen to the dispatch variable even if UI language changes later.
        i18n.set_language('zh')
        zh_packet = present(d)
        self.assertEqual(zh_packet['dispatch_input']['name'], '【gpt-6.1-sol】implementer')

        # Hook SubagentStart delivers stored name; PostToolUse records native nickname observed.
        kw = dict(tool_name='spawn_agent', tool_use_id='call-1',
                  tool_input={'agent_type': 'crew_implementer', 'task_name': en_packet['dispatch_input']['task_name'],
                              'message': en_packet['dispatch_input']['message']})
        workflow_hooks.handle(dict(hook_event_name='PreToolUse', session_id='sess-1', **kw), self.project)
        workflow_hooks.handle(dict(hook_event_name='PostToolUse', session_id='sess-1',
                                   tool_response={'agent_id': 'w1', 'nickname': 'gpt-6-1-sol implementer'}, **kw), self.project)
        sub_out = workflow_hooks.handle(dict(hook_event_name='SubagentStart', session_id='sess-1', agent_id='w1', agent_type='crew_implementer'), self.project)
        self.assertIn('【gpt-6.1-sol】implementer', sub_out['hookSpecificOutput']['additionalContext'])
        self.assertEqual(report(store.read(task))['assignment']['native_nickname_observed'], 'gpt-6-1-sol implementer')
        workflow_hooks.handle(dict(hook_event_name='SubagentStop', session_id='sess-1', agent_id='w1', agent_type='crew_implementer'), self.project)

        # Deliver, review (dispatched in zh), and check report preserves each stage's stored name.
        d = store.read(task)
        d = store.change(task, d['version'], 'deliver', files=['app.py'], summary='done', tests='ok')
        d = store.change(task, d['version'], 'dispatch')
        rev_pkt = present(d)['dispatch_input']
        self.assertEqual(rev_pkt['name'], '【gpt-6-astra】审查者')
        self.assertEqual(rev_pkt['task_name'], 'gpt_6_astra_reviewer_r2')
        kw2 = dict(tool_name='spawn_agent', tool_use_id='call-2',
                   tool_input={'agent_type': 'crew_reviewer', 'task_name': rev_pkt['task_name'], 'message': rev_pkt['message']})
        workflow_hooks.handle(dict(hook_event_name='PreToolUse', session_id='sess-1', **kw2), self.project)
        workflow_hooks.handle(dict(hook_event_name='PostToolUse', session_id='sess-1', tool_response={'agent_id': 'r1'}, **kw2), self.project)
        workflow_hooks.handle(dict(hook_event_name='SubagentStart', session_id='sess-1', agent_id='r1', agent_type='crew_reviewer'), self.project)
        workflow_hooks.handle(dict(hook_event_name='SubagentStop', session_id='sess-1', agent_id='r1', agent_type='crew_reviewer'), self.project)
        d = store.read(task)
        d = store.change(task, d['version'], 'review', verdict='pass', summary='good')

        rep = report(d)
        self.assertEqual(rep['delivery']['subagent_name'], '【gpt-6.1-sol】implementer')
        self.assertEqual(rep['review']['subagent_name'], '【gpt-6-astra】审查者')

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
            self.assertIn('"subagent_name":"【sonnet】审查者"', ctx)

            i18n.set_language('en')
            out_en = context_hooks.handle({
                'hook_event_name': 'SubagentStart',
                'session_id': 'claude-sess',
                'agent_id': 'a2',
                'agent_type': 'crew-reviewer',
            }, self.project)
            self.assertIn('"subagent_name":"【sonnet】reviewer"', out_en['hookSpecificOutput']['additionalContext'])
        finally:
            hosts.select('codex')

    def test_coordinator_presets_include_naming_rule(self):
        for host in ('codex', 'claude'):
            hosts.select(host)
            self.assertIn('【模型名】角色名', core.presets('zh')['coordinator']['instructions'])
            self.assertIn('【model】role', core.presets('en')['coordinator']['instructions'])
        hosts.select('codex')


if __name__ == '__main__':
    unittest.main()
