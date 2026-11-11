"""Claude Code host: detection, independent configuration and tasks, exports, hooks."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crew import core as c
from crew import hosts
from crew import wizard
from crew.context_store import ContextStore

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'bin' / 'crew'
MARKERS = ('PLUGIN_ROOT', 'CLAUDE_PLUGIN_ROOT', 'CLAUDE_PROJECT_DIR', 'CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CREW_HOST', 'CREW_DATA_HOME')


class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir(); (self.project / '.git').mkdir()
        clean = {k: v for k, v in os.environ.items() if k not in MARKERS}
        clean.update(CODEX_HOME=str(self.root / 'codex'), CLAUDE_CONFIG_DIR=str(self.root / 'claude'), LANG='en_US.UTF-8')
        clean.pop('LC_ALL', None); clean.pop('CREW_LANG', None)
        self.env = patch.dict(os.environ, clean, clear=True); self.env.start()
        hosts.select('claude')
        self.role = {'model': 'claude-opus-5-5', 'effort': 'max', 'description': 'Reviews: "quoted" text', 'instructions': 'Check the code.\nReport evidence.'}
        self.codex_role = {'model': 'gpt-example', 'effort': 'minimal', 'description': 'Codex side', 'instructions': 'Codex prompt'}
    def tearDown(self):
        self.env.stop(); hosts.select('codex'); hosts.CURRENT, hosts.SOURCE, hosts.DETECTED = 'codex', 'default', (None, None); self.tmp.cleanup()
    def use(self, host): hosts.select(host)
    def cli(self, *args, input=None, env=None):
        return subprocess.run([sys.executable, str(CLI), *args, '--project', str(self.project)], input=input, text=True, capture_output=True,
                              env=dict(os.environ, **(env or {})))
    def hook(self, event, *args, env=None):
        r = self.cli('hook', 'receive', *args, input=json.dumps(dict({'cwd': str(self.project), 'session_id': 'sess-1'}, **event)), env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    # ---- detection ----
    def test_detection_prefers_codex_marker_over_compat_variable(self):
        # Codex exports CLAUDE_PLUGIN_ROOT for plugin compatibility; it must not read as Claude Code.
        self.assertEqual(hosts.detect({'PLUGIN_ROOT': '/p', 'CLAUDE_PLUGIN_ROOT': '/p'}), ('codex', 'PLUGIN_ROOT'))
        self.assertEqual(hosts.detect({'CLAUDE_PLUGIN_ROOT': '/p'}), ('claude', 'CLAUDE_PLUGIN_ROOT'))
        self.assertEqual(hosts.detect({'CLAUDECODE': '1'}), ('claude', 'CLAUDECODE'))
        self.assertEqual(hosts.detect({}), (None, None))
    def test_selection_order(self):
        self.assertEqual(hosts.select(None, {}), 'codex'); self.assertEqual(hosts.SOURCE, 'default')
        self.assertEqual(hosts.select(None, {'CLAUDECODE': '1'}), 'claude'); self.assertEqual(hosts.SOURCE, 'environment')
        self.assertEqual(hosts.select(None, {'CLAUDECODE': '1', 'CREW_HOST': 'codex'}), 'codex'); self.assertEqual(hosts.SOURCE, 'CREW_HOST')
        self.assertEqual(hosts.select('claude', {'PLUGIN_ROOT': '/p', 'CREW_HOST': 'codex'}), 'claude'); self.assertEqual(hosts.mismatch(), 'codex')
    def test_cli_reports_detected_host(self):
        data = json.loads(self.cli('preflight', env={'CLAUDECODE': '1'}).stdout); self.assertEqual(data['host'], 'claude')
        data = json.loads(self.cli('preflight').stdout); self.assertEqual(data['host'], 'codex')
        data = json.loads(self.cli('doctor', '--host', 'claude').stdout)
        self.assertEqual(data['host_source'], 'flag'); self.assertEqual(data['home'], str(self.root / 'claude')); self.assertNotIn('agents_file', data)

    # ---- independent configuration ----
    def test_homes_are_separate(self):
        self.assertEqual(c.path('global', self.project), self.root / 'claude' / 'crew' / 'config.json')
        self.use('codex'); self.assertEqual(c.path('global', self.project), self.root / 'codex' / 'crew' / 'config.json')
    def test_roles_do_not_cross_hosts(self):
        c.role_write('project', self.project, 'reviewer', self.role, 'create')
        self.assertEqual(c.effective(self.project)['roles']['reviewer']['effort'], 'max')
        self.use('codex')
        self.assertEqual(c.effective(self.project)['roles'], {}); self.assertEqual(c.preflight(self.project)['roles_for_other_hosts_only'], ['reviewer'])
        c.role_write('project', self.project, 'reviewer', self.codex_role, 'create')
        stored = json.loads(c.path('project', self.project).read_text())['roles']['reviewer']
        self.assertEqual(stored['model'], 'gpt-example'); self.assertEqual(stored['hosts']['claude'], self.role)
        c.role_write('project', self.project, 'reviewer', {'instructions': 'Codex only edit'}, 'update')
        c.role_write('project', self.project, 'reviewer', None, 'disable')
        self.use('claude')
        role = c.effective(self.project)['roles']['reviewer']
        self.assertEqual(role['instructions'], self.role['instructions']); self.assertNotIn('disabled', role)
    def test_delete_keeps_other_host(self):
        self.use('codex'); c.role_write('project', self.project, 'reviewer', self.codex_role, 'create')
        self.use('claude'); c.role_write('project', self.project, 'reviewer', self.role, 'create')
        c.role_write('project', self.project, 'reviewer', None, 'delete')
        self.assertEqual(json.loads(c.path('project', self.project).read_text())['roles']['reviewer'], self.codex_role)
        with self.assertRaises(c.Error): c.role_write('project', self.project, 'reviewer', None, 'delete')
        self.use('codex'); c.role_write('project', self.project, 'reviewer', None, 'delete')
        self.assertEqual(json.loads(c.path('project', self.project).read_text())['roles'], {})
    def test_efforts_are_per_host(self):
        with self.assertRaises(c.Error): c.role_write('project', self.project, 'a', dict(self.role, effort='minimal'), 'create')
        self.use('codex')
        with self.assertRaises(c.Error): c.role_write('project', self.project, 'a', dict(self.codex_role, effort='max'), 'create')
    def test_enabled_is_per_host(self):
        c.set_enabled('project', self.project, False)
        self.assertEqual(c.preflight(self.project)['status'], 'disabled')
        self.use('codex'); self.assertTrue(c.effective(self.project)['enabled'])
        c.set_enabled('project', self.project, False); c.set_enabled('project', self.project, True)
        self.use('claude'); self.assertFalse(c.effective(self.project)['enabled'])
    def test_mask_and_inheritance(self):
        c.role_write('global', self.project, 'reviewer', self.role, 'create')
        c.role_write('project', self.project, 'reviewer', None, 'disable')
        self.assertEqual(c.preflight(self.project)['roles'], [])
        c.role_write('project', self.project, 'reviewer', None, 'enable')
        self.assertEqual(c.effective(self.project)['roles']['reviewer']['source'], 'global')
    def test_invalid_hosts_section_rejected(self):
        for bad in ({'hosts': {'codex': self.codex_role}}, {'hosts': {'claude': 'x'}}, {'hosts': {'other': {}}}, {'hosts': {}}):
            with self.assertRaises(c.Error): c.validate_role('reviewer', bad)
        c.validate_role('reviewer', {'hosts': {'claude': self.role}})
    def test_create_copies_text_from_other_host_once(self):
        self.use('codex'); c.role_write('project', self.project, 'architect', self.codex_role, 'create')
        r = self.cli('role', 'create', 'architect', '--host', 'claude', '--model', 'claude-opus-5-5', '--effort', 'high')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads(r.stdout)['copied_from'], {'host': 'codex', 'fields': ['description', 'instructions']})
        self.use('claude'); role = c.effective(self.project)['roles']['architect']
        self.assertEqual((role['description'], role['instructions'], role['model']), ('Codex side', 'Codex prompt', 'claude-opus-5-5'))
        self.use('codex'); c.role_write('project', self.project, 'architect', {'instructions': 'changed later'}, 'update')
        self.use('claude'); self.assertEqual(c.effective(self.project)['roles']['architect']['instructions'], 'Codex prompt')
    def test_presets_install_per_host(self):
        c.install_presets('project', self.project, ['reviewer'], 'sonnet', 'high')
        self.use('codex'); self.assertEqual(c.install_presets('project', self.project, ['reviewer'], 'gpt-example', 'low')['added'], ['reviewer'])
        self.use('claude'); self.assertEqual(c.install_presets('project', self.project, ['reviewer'], 'opus', 'low')['skipped_existing'], ['reviewer'])
        self.assertEqual(c.effective(self.project)['roles']['reviewer']['model'], 'sonnet')

    # ---- export ----
    def test_export_markdown_agent(self):
        c.role_write('project', self.project, 'reviewer', self.role, 'create')
        self.assertEqual(c.preflight(self.project)['status'], 'needs_export')
        r = c.export_agents(self.project, apply=True)
        self.assertEqual(r['directory'], str(self.project / '.claude' / 'agents')); self.assertIn('restart', r['note'])
        text = (self.project / '.claude' / 'agents' / 'crew-reviewer.md').read_text()
        head, body = text.split('\n---\n', 1)
        self.assertEqual(head.splitlines(), ['---', hosts.HEADER, 'name: crew-reviewer', 'description: "Reviews: \\"quoted\\" text"', 'model: "claude-opus-5-5"', 'effort: max'])
        self.assertEqual(body, 'Check the code.\nReport evidence.\n')
        self.assertEqual(c.preflight(self.project)['status'], 'ready')
        self.assertFalse((self.project / '.codex').exists())
        self.assertNotIn('note', c.export_agents(self.project, apply=True))
    def test_export_empty_prompt_and_drift(self):
        c.role_write('project', self.project, 'blank', dict(self.role, instructions=''), 'create'); c.export_agents(self.project, apply=True)
        self.assertTrue((self.project / '.claude' / 'agents' / 'crew-blank.md').read_text().endswith('effort: max\n---\n'))
        c.role_write('project', self.project, 'blank', {'effort': 'low'}, 'update')
        self.assertEqual(c.preflight(self.project)['export_drift'], ['blank'])
        (self.project / '.claude' / 'agents' / 'crew-blank.md').write_text('---\n' + hosts.HEADER + '\nname: crew-blank\n---\nhand edit\n')
        self.assertEqual(c.preflight(self.project)['export_drift'], ['blank'])
    def test_export_skips_roles_of_other_host(self):
        self.use('codex'); c.role_write('project', self.project, 'backend', self.codex_role, 'create')
        self.use('claude'); r = c.export_agents(self.project, apply=True)
        self.assertEqual(r['write'], []); self.assertEqual(r['skipped_not_configured_for_host'], ['backend'])
        self.assertFalse(list((self.project / '.claude' / 'agents').glob('*.md')))
    def test_export_collisions_and_clean(self):
        c.role_write('project', self.project, 'reviewer', self.role, 'create')
        dest = self.project / '.claude' / 'agents'; dest.mkdir(parents=True)
        (dest / 'mine.md').write_text('---\nname: "crew-reviewer"\ndescription: mine\n---\n')
        with self.assertRaises(c.Error): c.export_agents(self.project, apply=True)
        (dest / 'mine.md').write_text('---\nname: something-else\ndescription: mine\n---\n')
        (dest / 'crew-reviewer.md').write_text('user content')
        with self.assertRaises(c.Error): c.export_agents(self.project, apply=True)
        (dest / 'crew-reviewer.md').unlink(); c.export_agents(self.project, apply=True)
        c.role_write('project', self.project, 'reviewer', None, 'disable')
        self.assertEqual(c.preflight(self.project)['stale_exports'], ['reviewer'])
        self.assertEqual(c.export_agents(self.project, apply=True)['remove'], ['crew-reviewer.md'])
        self.assertEqual(sorted(p.name for p in dest.glob('*.md')), ['mine.md'])
    def test_global_export_and_no_entry_file(self):
        c.role_write('global', self.project, 'reviewer', self.role, 'create'); c.role_write('project', self.project, 'local', self.role, 'create')
        r = c.export_agents(self.project, 'global', apply=True)
        self.assertEqual(r['write'], ['crew-reviewer.md']); self.assertTrue((self.root / 'claude' / 'agents' / 'crew-reviewer.md').exists())
        self.assertEqual(c.setup(True)['status'], 'not_applicable'); self.assertEqual(c.uninstall(True)['status'], 'not_applicable')
        self.assertFalse((self.root / 'claude' / 'CLAUDE.md').exists()); self.assertFalse((self.root / 'claude' / 'AGENTS.md').exists())
        self.assertEqual(self.cli('migrate', '--host', 'claude').returncode, 2)

    # ---- wizard ----
    def test_wizard_writes_claude_settings_only(self):
        self.use('codex'); c.role_write('project', self.project, 'reviewer', dict(c.presets('en')['reviewer'], model='gpt-example', effort='low'), 'create')
        before = wizard.advance('project', self.project)
        self.use('claude'); state = wizard.advance('project', self.project)
        self.assertEqual(state['host'], 'claude'); self.assertTrue(state['draft_file'].endswith('onboarding.claude.json'))
        self.assertEqual(state['step']['existing_roles'], [])
        state = wizard.advance('project', self.project, ['reviewer'], 'roles', state['revision'])
        state = wizard.advance('project', self.project, 'opus', 'reviewer.model', state['revision'])
        self.assertEqual(state['step']['choices'], list(hosts.EFFORTS['claude']))
        with self.assertRaises(c.Error): wizard.advance('project', self.project, 'minimal', 'reviewer.effort', state['revision'])
        state = wizard.advance('project', self.project, 'max', 'reviewer.effort', state['revision'])
        token = state['step']['preview']['confirmation_token']
        state = wizard.advance('project', self.project, True, 'confirm', state['revision'], True, token)
        self.assertEqual(state['status'], 'complete')
        stored = json.loads(c.path('project', self.project).read_text())['roles']['reviewer']
        self.assertEqual(stored['model'], 'gpt-example'); self.assertEqual(stored['hosts']['claude']['effort'], 'max')
        self.use('codex'); self.assertEqual(wizard.advance('project', self.project)['revision'], before['revision'])

    # ---- independent tasks ----
    def test_task_stores_are_separate(self):
        claude = ContextStore(self.project); mine = claude.open_session('claude', 'one')
        self.assertEqual(claude.path, self.root / 'claude' / 'crew' / 'context.claude.sqlite3')
        claude.decision_put('language', 'Chinese reviews', scope='global', expected_version=0)
        self.use('codex'); codex = ContextStore(self.project)
        self.assertEqual(codex.path, self.root / 'codex' / 'crew' / 'context.sqlite3')
        self.assertEqual(codex.list_tasks(), []); self.assertEqual(codex.decision_list(scope='global'), [])
        with self.assertRaises(c.Error): codex.read(mine['task_id'])
    def test_task_stores_are_separate_with_same_data_directory(self):
        directory = self.root / 'shared'
        claude = ContextStore(self.project, data_home=directory)
        task = claude.open_session('claude', 'same-session')
        self.use('codex')
        codex = ContextStore(self.project, data_home=directory)
        self.assertNotEqual(codex.path, claude.path)
        with self.assertRaises(c.Error):
            codex.read(task['task_id'])

    def test_context_provider_defaults_to_host(self):
        r = self.cli('context', 'open', '--session', 'manual', '--host', 'claude'); self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads(r.stdout)['session']['provider'], 'claude')
        self.assertEqual(json.loads(self.cli('context', 'list', '--host', 'codex').stdout), [])

    # ---- hooks ----
    def test_session_start_reports_host_to_user_and_model(self):
        out = self.hook({'hook_event_name': 'SessionStart', 'source': 'startup'}, '--host', 'claude', env={'CLAUDE_PLUGIN_ROOT': str(ROOT), 'CLAUDE_PROJECT_DIR': str(self.project)})
        self.assertIn('Claude Code environment detected (CLAUDE_PROJECT_DIR)', out['systemMessage'])
        self.assertIn('Using Claude Code roles and tasks only: 0 role(s), status needs_setup', out['systemMessage'])
        context = out['hookSpecificOutput']['additionalContext']
        self.assertIn('"host":"claude"', context); self.assertIn('Crew configuration: {"host":"claude"', context)
        self.use('claude'); store = ContextStore(self.project)
        self.assertEqual(store.list_tasks()[0]['session_count'], 1)
        self.assertEqual(store.open_session('claude', 'sess-1')['created'], False)
        self.assertTrue((self.root / 'claude' / 'crew' / 'receipts').is_dir()); self.assertFalse((self.root / 'codex').exists())
    def test_banner_is_localized_and_skipped_on_compact(self):
        env = {'CLAUDE_PLUGIN_ROOT': str(ROOT), 'LANG': 'zh_CN.UTF-8'}
        out = self.hook({'hook_event_name': 'SessionStart', 'source': 'resume'}, '--host', 'claude', env=env)
        self.assertIn('检测到 Claude Code 环境', out['systemMessage'])
        out = self.hook({'hook_event_name': 'SessionStart', 'source': 'compact'}, '--host', 'claude', env=env)
        self.assertNotIn('systemMessage', out); self.assertIn('orientation', out['hookSpecificOutput']['additionalContext'])
    def test_banner_mentions_roles_of_other_host(self):
        self.use('codex'); c.role_write('project', self.project, 'backend', self.codex_role, 'create')
        out = self.hook({'hook_event_name': 'SessionStart', 'source': 'startup'}, '--host', 'claude', env={'CLAUDE_PLUGIN_ROOT': str(ROOT)})
        self.assertIn('1 role(s) are configured for the other host only', out['systemMessage'])
    def test_wrong_hooks_file_is_inert(self):
        # Claude Code's hooks file running under Codex (which also sets CLAUDE_PLUGIN_ROOT).
        env = {'PLUGIN_ROOT': str(ROOT), 'CLAUDE_PLUGIN_ROOT': str(ROOT)}
        out = self.hook({'hook_event_name': 'SessionStart', 'source': 'startup'}, '--host', 'claude', env=env)
        self.assertEqual(list(out), ['systemMessage']); self.assertIn('the Claude Code hooks file was loaded inside Codex (PLUGIN_ROOT)', out['systemMessage'])
        self.assertEqual(self.hook({'hook_event_name': 'UserPromptSubmit', 'prompt': 'hi'}, '--host', 'claude', env=env), {})
        out = self.hook({'hook_event_name': 'SessionStart'}, '--host', 'codex', env={'CLAUDE_PLUGIN_ROOT': str(ROOT)})
        self.assertIn('the Codex hooks file was loaded inside Claude Code', out['systemMessage'])
        self.assertFalse((self.root / 'claude').exists()); self.assertFalse((self.root / 'codex').exists())
    def test_codex_hooks_file_still_works_under_codex(self):
        env = {'PLUGIN_ROOT': str(ROOT), 'CLAUDE_PLUGIN_ROOT': str(ROOT)}
        out = self.hook({'hook_event_name': 'SessionStart', 'source': 'startup'}, '--host', 'codex', env=env)
        self.assertIn('Codex environment detected (PLUGIN_ROOT)', out['systemMessage'])
        self.use('codex'); self.assertEqual(ContextStore(self.project).open_session('codex', 'sess-1')['created'], False)
    def test_subagent_links_to_parent_task(self):
        env = {'CLAUDE_PLUGIN_ROOT': str(ROOT)}
        self.hook({'hook_event_name': 'UserPromptSubmit', 'prompt': 'DO_NOT_STORE'}, '--host', 'claude', env=env)
        out = self.hook({'hook_event_name': 'SubagentStart', 'agent_id': 'agent-7', 'agent_type': 'crew-reviewer'}, '--host', 'claude', env=env)
        self.assertEqual(out['hookSpecificOutput']['hookEventName'], 'SubagentStart')
        store = ContextStore(self.project); parent = store.open_session('claude', 'sess-1')
        self.assertEqual(store.open_session('claude', 'sess-1/agent/agent-7')['task_id'], parent['task_id'])
        for p in (self.root / 'claude').rglob('*'):
            if p.is_file(): self.assertNotIn(b'DO_NOT_STORE', p.read_bytes())
    def test_hook_never_exits_nonzero(self):
        env = {'CLAUDE_PLUGIN_ROOT': str(ROOT)}
        for payload in ('', '!', '[]', '{"hook_event_name":"UserPromptSubmit","cwd":5}'):
            r = self.cli('hook', 'receive', '--host', 'claude', input=payload, env=env)
            self.assertEqual(r.returncode, 0, payload); json.loads(r.stdout)
        (self.root / 'claude').mkdir(); (self.root / 'claude' / 'crew').write_text('not a directory')
        r = self.cli('hook', 'receive', '--host', 'claude', input=json.dumps({'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.project), 'session_id': 's'}), env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn('could not be read', r.stdout)
    def test_shipped_hook_files(self):
        claude = json.loads((ROOT / 'hooks' / 'hooks.json').read_text())['hooks']; codex = json.loads((ROOT / 'hooks' / 'codex.json').read_text())['hooks']
        for config, host, variable in ((claude, 'claude', '${CLAUDE_PLUGIN_ROOT}'), (codex, 'codex', '${PLUGIN_ROOT}')):
            self.assertEqual(sorted(config), ['PostCompact', 'SessionEnd', 'SessionStart', 'SubagentStart', 'UserPromptSubmit'])
            for groups in config.values():
                self.assertEqual(groups[0]['hooks'][0]['command'], f'python3 "{variable}/bin/crew" hook receive --host {host}')
        self.assertEqual(claude['SubagentStart'][0]['matcher'], '^crew-'); self.assertNotIn('matcher', codex['SubagentStart'][0])
        self.assertEqual(json.loads((ROOT / 'plugin.json').read_text())['extensions']['com.openai']['hooks'], './hooks/codex.json')
        self.assertEqual(json.loads((ROOT / '.claude-plugin' / 'plugin.json').read_text())['version'], json.loads((ROOT / 'plugin.json').read_text())['version'])

    # ---- locks ----
    @unittest.skipIf(c.fcntl is None, 'stale lock recovery requires flock')
    def test_lock_left_by_dead_process_is_released(self):
        target = self.root / 'state.json'; gone = subprocess.Popen([sys.executable, '-c', 'pass']); gone.wait()
        Path(str(target) + '.lock').write_text(str(gone.pid))
        with c.lock(target): self.assertEqual(Path(str(target) + '.lock').read_text(), str(os.getpid()))
        self.assertFalse(Path(str(target) + '.lock').exists())
    @unittest.skipIf(c.fcntl is None, 'stale lock recovery requires flock')
    def test_stale_lock_recovery_leaves_replacement_inode_alone(self):
        lock = self.root / 'state.json.lock'
        lock.write_text('123')
        replacement = self.root / 'replacement'
        replacement.write_text('123')

        def owner_is_gone(pid, signal):
            replacement.replace(lock)
            raise ProcessLookupError

        with patch.object(c.os, 'kill', side_effect=owner_is_gone):
            self.assertFalse(c._release_dead_lock(lock))
        self.assertEqual(lock.read_text(), '123')

    def test_lock_held_by_live_process_is_respected(self):
        target = self.root / 'state.json'; Path(str(target) + '.lock').write_text(str(os.getpid()))
        with self.assertRaises(c.Error):
            with c.lock(target): pass
        self.assertTrue(Path(str(target) + '.lock').exists())


if __name__ == '__main__': unittest.main()
