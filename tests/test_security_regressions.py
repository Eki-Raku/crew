"""Regression tests use disposable directories only, never live host sessions."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest.mock import patch

from crew import cli, core as c, hosts, i18n, wizard
from crew.context_store import ContextStore

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'bin' / 'crew'


class SecurityRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir()
        (self.project / '.git').mkdir()
        self.outside = self.root / 'outside'; self.outside.mkdir()
        self.env = patch.dict(os.environ, {'CREW_HOST': 'codex', 'CODEX_HOME': str(self.root / 'codex'),
            'CLAUDE_CONFIG_DIR': str(self.root / 'claude'), 'CREW_DATA_HOME': str(self.root / 'data')})
        self.env.start()
        self.previous_host, self.previous_lang = hosts.CURRENT, i18n.LANG
        hosts.select('codex'); i18n.set_language('en')
        self.role = dict(model='example', effort='low', description='Example', instructions='User content')

    def tearDown(self):
        hosts.select(self.previous_host); i18n.set_language(self.previous_lang)
        self.env.stop(); self.tmp.cleanup()

    def run_cli(self, *args, payload=None, timeout=5):
        return subprocess.run([sys.executable, str(CLI), *args, '--project', str(self.project)],
                              input=payload, capture_output=True, text=True, timeout=timeout)

    def test_project_config_directory_symlink_rejects_all_writers(self):
        (self.project / '.crew').symlink_to(self.outside, target_is_directory=True)
        writers = [lambda: c.role_write('project', self.project, 'builder', self.role, 'create'),
                   lambda: c.set_enabled('project', self.project, False),
                   lambda: c.install_presets('project', self.project, ['reviewer'], 'example', 'low'),
                   lambda: wizard.advance('project', self.project)]
        for writer in writers:
            with self.subTest(writer=writer), self.assertRaises(c.Error): writer()
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_dangling_config_and_onboarding_leaf_symlinks_are_rejected(self):
        directory = self.project / '.crew'; directory.mkdir()
        for filename in ('config.json', 'onboarding.json'):
            with self.subTest(filename=filename):
                link = directory / filename; link.symlink_to(self.outside / filename)
                with self.assertRaises(c.Error):
                    if filename == 'config.json': c.set_enabled('project', self.project, True)
                    else: wizard.advance('project', self.project)
                self.assertTrue(link.is_symlink()); self.assertFalse((self.outside / filename).exists())
                link.unlink()

    def test_export_directory_links_never_clean_global_files(self):
        for host in hosts.HOSTS:
            for linked in ('parent', 'agents'):
                for apply in (False, True):
                    with self.subTest(host=host, linked=linked, apply=apply):
                        hosts.select(host)
                        spec = hosts.export()
                        target = self.outside / f'{host}-{linked}-{apply}' / 'agents'
                        target.mkdir(parents=True)
                        sentinel = target / spec.filename('builder')
                        sentinel.write_text(spec.render('builder', self.role))
                        base = self.project / spec.project_dir[0]
                        if linked == 'parent': base.symlink_to(target.parent, target_is_directory=True)
                        else:
                            base.mkdir(); (base / 'agents').symlink_to(target, target_is_directory=True)
                        with self.assertRaises(c.Error): c.export_agents(self.project, apply=apply, clean=True)
                        self.assertTrue(sentinel.exists())
                        if linked == 'parent': base.unlink()
                        else: (base / 'agents').unlink(); base.rmdir()

    def test_export_leaf_symlink_is_rejected_before_any_write(self):
        c.role_write('project', self.project, 'builder', self.role, 'create')
        dest = self.project / '.codex' / 'agents'; dest.mkdir(parents=True)
        target = self.outside / 'agent.toml'
        link = dest / 'crew-builder.toml'; link.symlink_to(target)
        for apply in (False, True):
            with self.subTest(apply=apply), self.assertRaises(c.Error): c.export_agents(self.project, apply=apply)
        self.assertTrue(link.is_symlink()); self.assertFalse(target.exists())

    def test_lock_symlink_is_never_read_or_reclaimed(self):
        target = self.project / 'state.json'
        outside = self.outside / 'lock'; outside.write_text('123456789')
        link = Path(str(target) + '.lock'); link.symlink_to(outside)
        with self.assertRaises(c.Error):
            with c.lock(target): pass
        self.assertEqual(outside.read_text(), '123456789'); self.assertTrue(link.is_symlink())

    def test_atomic_rechecks_parent_before_replace(self):
        directory = self.project / '.crew'; directory.mkdir()
        target = directory / 'config.json'
        displaced = self.project / 'original-crew'
        real_fsync = os.fsync
        def swap(fd):
            real_fsync(fd)
            directory.rename(displaced)
            directory.symlink_to(self.outside, target_is_directory=True)
        with patch.object(c.os, 'fsync', side_effect=swap), self.assertRaises(c.Error): c.atomic(target, '{}')
        self.assertEqual(list(self.outside.iterdir()), [])
        self.assertEqual(list(displaced.iterdir()), [])

    def test_nonregular_config_is_rejected_without_blocking(self):
        directory = self.project / '.crew'; directory.mkdir()
        os.mkfifo(directory / 'config.json')
        with self.assertRaises(c.Error): c.effective(self.project)

    def test_unknown_role_fields_fail_explicitly_for_each_host(self):
        for host in hosts.HOSTS:
            hosts.select(host)
            for key in ('tools', 'permissionMode', 'skills', 'future_option'):
                with self.subTest(host=host, key=key), self.assertRaisesRegex(c.Error, 'Unsupported .* role fields: ' + key):
                    c.role_write('project', self.project, 'builder', dict(self.role, **{key: 'value'}), 'create')
        self.assertFalse(c.path('project', self.project).exists())

    def test_existing_unsupported_config_is_not_exported_or_rewritten(self):
        hosts.select('claude')
        config = {'schema_version': 1, 'roles': {'builder': {'hosts': {'claude': dict(self.role, tools=['Read'])}}}}
        target = c.path('project', self.project); c.save(target, config)
        before = target.read_bytes()
        with self.assertRaisesRegex(c.Error, 'tools'): c.export_agents(self.project, apply=True)
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse((self.project / '.claude').exists())

    def test_toml_all_ascii_controls_and_unicode_round_trip(self):
        text = ''.join(chr(i) for i in range(128)) + '中文 👩🏽‍💻 café \\ "\n'
        rendered = hosts.CodexExport.render('builder', dict(self.role, instructions=text, description=text))
        parsed = tomllib.loads(rendered)
        self.assertEqual(parsed['developer_instructions'], text)
        self.assertEqual(parsed['description'], text)
        self.assertNotIn('\x7f', rendered)

    def test_context_state_and_delta_are_language_invariant(self):
        store = ContextStore(self.project)
        opened = store.open_session('codex', 'one')
        original = {key: ['Current prompt: checkpoint', 'Review/edit prompts and model choices, then preview export.']
                    for key in ('next', 'summary', 'goal', 'constraints', 'decisions', 'progress', 'blockers', 'evidence')}
        store.patch(opened['task_id'], opened['version'], original)
        for extra, key in (([], 'state'), (['--since', str(opened['version'])], 'changes')):
            en = self.run_cli('--lang', 'en', 'context', 'read', '--task', opened['task_id'], *extra)
            zh = self.run_cli('--lang', 'zh', 'context', 'read', '--task', opened['task_id'], *extra)
            self.assertEqual(en.returncode, 0, en.stdout); self.assertEqual(zh.returncode, 0, zh.stdout)
            self.assertEqual(json.loads(en.stdout)[key], original)
            self.assertEqual(json.loads(zh.stdout)[key], original)

    def test_ui_hints_still_translate(self):
        i18n.set_language('zh')
        text = 'Review/edit prompts and model choices, then preview export.'
        self.assertNotEqual(i18n.localize({'next': text})['next'], text)
        self.assertEqual(i18n.localize({'state': {'next': text}})['state']['next'], text)

    def test_receipt_contention_returns_before_host_deadline(self):
        path = c.receipt_path(self.project); path.parent.mkdir(parents=True)
        Path(str(path) + '.lock').write_text(str(os.getpid()))
        before = time.monotonic()
        result = self.run_cli('hook', 'receive', payload=json.dumps({'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.project), 'session_id': 'busy'}))
        self.assertLess(time.monotonic() - before, 2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('could not be read', result.stdout)

    @unittest.skipUnless(hasattr(signal, 'setitimer'), 'Unix timer required')
    def test_entire_hook_has_an_outer_deadline(self):
        before = time.monotonic()
        with patch.object(cli, '_hook_receive', side_effect=lambda *args: time.sleep(5)):
            result = cli.hook_receive(str(self.project))
        self.assertLess(time.monotonic() - before, 3)
        self.assertIn('time budget', result['systemMessage'])

    def test_manifests_match_version_and_referenced_files(self):
        from crew import __version__
        for filename in ('plugin.json', '.claude-plugin/plugin.json'):
            self.assertEqual(json.loads((ROOT / filename).read_text())['version'], __version__)
        for filename in ('bin/crew', 'skills/crew/SKILL.md', 'hooks/codex.json', 'hooks/hooks.json', 'LICENSE'):
            self.assertTrue((ROOT / filename).is_file())


if __name__ == '__main__': unittest.main()
