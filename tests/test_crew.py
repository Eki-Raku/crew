import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crew import core as c
CLI = Path(__file__).resolve().parents[1] / 'bin' / 'crew'

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve(); self.project = self.root / 'project'; self.project.mkdir(); (self.project / '.git').mkdir()
        self.env = patch.dict(os.environ, {'CREW_HOST': 'codex', 'CODEX_HOME': str(self.root / 'custom home')}); self.env.start()
        self.role = {'model': 'example-model', 'effort': 'low', 'description': 'Implementation', 'instructions': 'Check the code.\nReport evidence.'}
    def tearDown(self): self.env.stop(); self.tmp.cleanup()
    def add(self, name='builder', scope='global'): return c.role_write(scope, self.project, name, self.role, 'create')
    def cli(self, *args, input=None):
        return subprocess.run([str(CLI), *args, '--project', str(self.project)], input=input, text=True, capture_output=True)
    def test_empty(self): self.assertEqual(c.preflight(self.project)['status'], 'needs_setup')
    def test_crud(self):
        self.add(); self.assertEqual(c.effective(self.project)['roles']['builder']['source'], 'global')
        c.role_write('global', self.project, 'builder', dict(self.role, effort='high'), 'update')
        self.assertEqual(c.effective(self.project)['roles']['builder']['effort'], 'high')
        c.role_write('global', self.project, 'builder', None, 'delete'); self.assertEqual(c.effective(self.project)['roles'], {})
    def test_override(self):
        self.add(); self.add(scope='project'); self.assertEqual(c.effective(self.project)['roles']['builder']['source'], 'project')
    def test_mask(self):
        self.add(); c.role_write('project', self.project, 'builder', None, 'disable'); self.assertEqual(c.preflight(self.project)['status'], 'needs_setup')
    def test_disabled(self):
        c.set_enabled('project', self.project, False); self.assertEqual(c.preflight(self.project)['status'], 'disabled')
    def test_validation(self):
        for name, value in [('../evil', self.role), ('builder', dict(self.role, effort='huge')), ('builder', dict(self.role, model=''))]:
            with self.assertRaises(c.Error): c.role_write('global', self.project, name, value, 'create')
    def test_corrupt(self):
        p = c.path('global', self.project); p.parent.mkdir(parents=True); p.write_text('{bad')
        with self.assertRaises(c.Error): c.effective(self.project)
    def test_future_schema(self):
        p = c.path('global', self.project); c.save(p, {'schema_version': 99, 'roles': {}})
        with self.assertRaises(c.Error): c.effective(self.project)
    def test_setup_preview(self):
        r = c.setup(); self.assertFalse(Path(r['target']).exists())
    def test_setup_idempotent(self):
        p = c.home() / 'AGENTS.md'; p.parent.mkdir(parents=True); p.write_text('Original\n')
        c.setup(True); one = p.read_text(); c.setup(True); self.assertEqual(p.read_text(), one)
        self.assertTrue(one.startswith('Original\n')); self.assertEqual(one.count(c.START), 1)
    def test_override_file(self):
        p = c.home() / 'AGENTS.override.md'; p.parent.mkdir(parents=True); p.write_text('Priority\n')
        self.assertEqual(c.setup(True)['target'], str(p))
    def test_empty_override(self):
        p = c.home() / 'AGENTS.override.md'; p.parent.mkdir(parents=True); p.touch()
        self.assertTrue(c.setup(True)['target'].endswith('AGENTS.md'))
    def test_uninstall_preserves_edits(self):
        c.setup(True); p = c.agents_target(); p.write_text(p.read_text() + 'User new work\n')
        c.uninstall(True); self.assertEqual(p.read_text(), 'User new work\n')
    def test_malformed_marker(self):
        p = c.home() / 'AGENTS.md'; p.parent.mkdir(parents=True); p.write_text(c.START)
        with self.assertRaises(c.Error): c.setup(True)
    def test_export_toml(self):
        self.add(); r = c.export_agents(self.project, apply=True)
        obj = tomllib.loads((Path(r['directory']) / 'crew-builder.toml').read_text())
        self.assertEqual(obj['model_reasoning_effort'], 'low'); self.assertEqual(obj['developer_instructions'], self.role['instructions'])
    def test_global_export_no_project_leak(self):
        self.add(scope='project'); self.assertEqual(c.export_agents(self.project, 'global')['write'], [])
    def test_export_collision(self):
        self.add(); dest = self.project / '.codex' / 'agents'; dest.mkdir(parents=True); (dest / 'crew-builder.toml').write_text('user content')
        with self.assertRaises(c.Error): c.export_agents(self.project, apply=True)
    def test_export_clean(self):
        self.add(); c.export_agents(self.project, apply=True); c.export_agents(self.project, apply=True, clean=True)
        self.assertFalse(list((self.project / '.codex' / 'agents').glob('*.toml')))
    def test_ping_not_real(self):
        r = self.cli('hook', 'ping'); self.assertEqual(r.returncode, 0); self.assertFalse(json.loads(r.stdout)['host_verified'])
    def test_hook_compaction(self):
        e = {'hook_event_name': 'PostCompact', 'session_id': 'one', 'trigger': 'auto', 'transcript_path': '/never/read', 'secret': 'not stored'}
        c.record(e, self.project); c.record(e, self.project, True)
        state = c.doctor(self.project)['hook_receipts']; self.assertEqual(state['auto_compactions']['one'], 1)
        self.assertNotIn('not stored', c.receipt_path(self.project).read_text())
    def test_hook_session_json(self):
        r = self.cli('hook', 'receive', input=json.dumps({'hook_event_name': 'SessionStart', 'cwd': str(self.project)}))
        self.assertEqual(r.returncode, 0); self.assertIn('needs_setup', json.loads(r.stdout)['hookSpecificOutput']['additionalContext'])
    def test_hook_bad_json(self):
        # Exit code 2 would block the user's prompt in both hosts; a bad payload is only a notice.
        r = self.cli('hook', 'receive', input='!'); self.assertEqual(r.returncode, 0); self.assertIn('systemMessage', json.loads(r.stdout))
    def test_no_tty(self): self.assertEqual(self.cli('tui').returncode, 2)
    def test_concurrent_updates(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(lambda n: self.add('role' + str(n)), range(8)))
        self.assertEqual(len(c.effective(self.project)['roles']), 8)
    def test_drift(self):
        self.add(); self.assertEqual(c.preflight(self.project)['status'], 'needs_export')
        c.export_agents(self.project, apply=True); self.assertEqual(c.preflight(self.project)['status'], 'ready')
        c.role_write('global', self.project, 'builder', {'effort': 'high'}, 'update')
        self.assertEqual(c.preflight(self.project)['export_drift'], ['builder'])
    def test_update_field(self):
        self.add(); r = self.cli('role', 'update', 'builder', '--scope', 'global', '--effort', 'high')
        self.assertEqual(r.returncode, 0); self.assertEqual(c.effective(self.project)['roles']['builder']['model'], 'example-model')
    def test_missing_answers(self): self.assertEqual(self.cli('init', '--answers', '/no/such/file').returncode, 2)
    def test_subdirectory(self):
        self.add(scope='project'); sub = self.project / 'src'; sub.mkdir()
        self.assertEqual(c.project_root(sub), self.project)
        self.assertIn('builder', c.effective(sub)['roles'])
    def test_collision_by_name(self):
        self.add(); dest = self.project / '.codex' / 'agents'; dest.mkdir(parents=True)
        (dest / 'mine.toml').write_text('name = "crew_builder"')
        with self.assertRaises(c.Error): c.export_agents(self.project, apply=True)

    def test_deleted_export_drift(self):
        self.add(); self.add('other'); c.export_agents(self.project, apply=True)
        c.role_write('global', self.project, 'other', None, 'delete')
        state = c.preflight(self.project)
        self.assertEqual(state['status'], 'needs_export'); self.assertEqual(state['stale_exports'], ['other'])
        c.export_agents(self.project, apply=True); self.assertEqual(c.preflight(self.project)['status'], 'ready')
    def test_disabled_export_drift(self):
        self.add(); c.export_agents(self.project, apply=True)
        c.role_write('global', self.project, 'builder', None, 'disable')
        self.assertEqual(c.preflight(self.project)['status'], 'needs_export')
        c.export_agents(self.project, apply=True); self.assertEqual(c.preflight(self.project)['status'], 'needs_setup')
    def test_project_disable_cleanup(self):
        self.add(); c.export_agents(self.project, apply=True); c.set_enabled('project', self.project, False)
        state = c.preflight(self.project)
        self.assertEqual(state['status'], 'needs_export'); self.assertFalse(state['enabled'])
        c.export_agents(self.project, apply=True); self.assertEqual(c.preflight(self.project)['status'], 'disabled')
    def test_role_reenable_preserves_definition(self):
        self.add(); self.cli('role', 'disable', 'builder', '--scope', 'global')
        self.cli('role', 'update', 'builder', '--scope', 'global', '--effort', 'high')
        result = self.cli('role', 'enable', 'builder', '--scope', 'global')
        self.assertEqual(result.returncode, 0)
        role = c.effective(self.project)['roles']['builder']
        self.assertFalse(role['disabled']); self.assertEqual(role['effort'], 'high')
        c.export_agents(self.project, apply=True); self.assertEqual(c.preflight(self.project)['status'], 'ready')
    def test_role_enable_mask_resumes_inheritance(self):
        self.add(); c.role_write('project', self.project, 'builder', None, 'disable')
        c.role_write('project', self.project, 'builder', None, 'enable')
        self.assertEqual(c.effective(self.project)['roles']['builder']['source'], 'global')
    def test_role_enable_missing_definition(self):
        c.role_write('global', self.project, 'builder', None, 'disable')
        with self.assertRaises(c.Error): c.role_write('global', self.project, 'builder', None, 'enable')
        c.role_write('global', self.project, 'builder', self.role, 'update')
        c.role_write('global', self.project, 'builder', None, 'enable')
        self.assertFalse(c.effective(self.project)['roles']['builder']['disabled'])

    def test_presets_available_without_config(self):
        result = self.cli('presets', 'list')
        presets = json.loads(result.stdout)['presets']
        self.assertEqual(len(presets), 5)
        self.assertTrue(all(v['instructions'].strip() for v in presets.values()))
        self.assertFalse(c.path('project', self.project).exists())
    def test_presets_install_and_upgrade_preserve(self):
        result = self.cli('presets', 'install', '--model', 'example-model', '--effort', 'low')
        self.assertEqual(result.returncode, 0)
        c.role_write('project', self.project, 'coordinator', {'instructions': 'My custom prompt'}, 'update')
        result = self.cli('presets', 'install', '--model', 'different-model', '--effort', 'high')
        self.assertEqual(len(json.loads(result.stdout)['skipped_existing']), 5)
        role = c.effective(self.project)['roles']['coordinator']
        self.assertEqual(role['instructions'], 'My custom prompt'); self.assertEqual(role['model'], 'example-model')
    def test_presets_preserve_inherited(self):
        self.add('reviewer'); c.install_presets('project', self.project, ['reviewer'], 'other-model', 'high')
        self.assertEqual(c.effective(self.project)['roles']['reviewer']['source'], 'global')
    def test_create_empty_agent_default(self):
        result = self.cli('role', 'create', 'blank', '--model', 'example-model', '--effort', 'low', '--description', 'Empty role')
        self.assertEqual(result.returncode, 0)
        role = json.loads(self.cli('role', 'show', 'blank').stdout)
        self.assertEqual(role['instructions'], ''); self.assertEqual(role['prompt_state'], 'empty_agent')
        c.export_agents(self.project, apply=True)
        obj = tomllib.loads((self.project / '.codex/agents/crew-blank.toml').read_text())
        self.assertEqual(obj['developer_instructions'], '')
        self.assertEqual(c.preflight(self.project)['status'], 'ready')
    def test_prompt_file_and_clear(self):
        self.add(scope='project'); prompt = self.root / 'prompt.md'; prompt.write_text('第一行\nSecond line\n')
        result = self.cli('role', 'update', 'builder', '--instructions-file', str(prompt))
        self.assertEqual(result.returncode, 0); self.assertEqual(c.effective(self.project)['roles']['builder']['instructions'], prompt.read_text())
        result = self.cli('role', 'update', 'builder', '--instructions', '')
        self.assertEqual(result.returncode, 0); self.assertEqual(c.effective(self.project)['roles']['builder']['prompt_state'], 'empty_agent')
    def test_empty_instructions_validation(self):
        c.validate_role('blank', dict(self.role, instructions=''))
        for value in (None, [], 1):
            with self.assertRaises(c.Error): c.validate_role('blank', dict(self.role, instructions=value))
    def test_tui_prompt_editor(self):
        from crew.cli import edit_prompt
        with patch('builtins.input', side_effect=['1', 'line 1', 'line 2', '.']): self.assertEqual(edit_prompt(), 'line 1\nline 2')
        with patch('builtins.input', side_effect=['3']): self.assertEqual(edit_prompt('old', True), '')
        with patch('builtins.input', side_effect=['4']): self.assertEqual(edit_prompt('old', True), 'old')

    def test_language_presets_and_separate_prompt_locale(self):
        zh = json.loads(self.cli('presets', 'list', '--lang', 'zh').stdout)
        en = json.loads(self.cli('presets', 'list', '--lang', 'en').stdout)
        self.assertIn('协调', zh['presets']['coordinator']['description'])
        self.assertIn('Coordinate', en['presets']['coordinator']['description'])
        result = self.cli('presets', 'install', 'tester', '--lang', 'zh', '--prompt-lang', 'en', '--model', 'example-model', '--effort', 'low')
        self.assertEqual(result.returncode, 0)
        self.assertTrue(c.effective(self.project)['roles']['tester']['instructions'].startswith('Verify'))
    def test_language_environment_and_errors(self):
        with patch.dict(os.environ, {'CREW_LANG': 'zh'}):
            result = self.cli('role', 'show', 'absent')
            self.assertEqual(json.loads(result.stdout)['message'], '未找到角色')
            result = self.cli('role', 'show', 'absent', '--lang', 'en')
            self.assertEqual(json.loads(result.stdout)['message'], 'Role not found')
    def test_language_help(self):
        result = self.cli('role', '--help', '--lang', 'zh')
        self.assertIn('用法', result.stdout); self.assertIn('UTF-8 提示词文件', result.stdout)
        result = self.cli('role', '--help', '--lang', 'en')
        self.assertIn('usage:', result.stdout); self.assertIn('UTF-8 prompt file', result.stdout)
    def test_language_switch_preserves_saved_prompt(self):
        self.cli('presets', 'install', 'reviewer', '--lang', 'zh', '--model', 'example-model', '--effort', 'low')
        before = c.effective(self.project)['roles']['reviewer']['instructions']
        after = json.loads(self.cli('role', 'show', 'reviewer', '--lang', 'en').stdout)['instructions']
        self.assertEqual(before, after)
    def test_ui_translation_and_license(self):
        from crew import i18n
        try:
            i18n.set_language('zh')
            self.assertEqual(i18n.t('Save? y/N'), '保存？y/N')
            self.assertEqual(i18n.t('Current prompt: hello'), '当前提示词：hello')
            self.assertEqual(i18n.localize({'instructions': 'Role not found'})['instructions'], 'Role not found')
        finally: i18n.set_language('en')
        self.assertIn('MIT License', (CLI.parents[1] / 'LICENSE').read_text())

if __name__ == '__main__': unittest.main()
