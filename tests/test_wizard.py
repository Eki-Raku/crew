import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from crew import core as c, wizard as w

class WizardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir(); (self.project / '.git').mkdir()
        self.env = patch.dict(os.environ, {'CREW_HOST': 'codex', 'CODEX_HOME': str(self.root / 'home')}); self.env.start()
    def tearDown(self): self.env.stop(); self.tmp.cleanup()
    def start(self): return w.advance('project', self.project)
    def answer(self, state, value, **kwargs):
        return w.advance('project', self.project, value, state['step']['id'], state['revision'], **kwargs)
    def ready(self):
        state = self.answer(self.start(), ['implementer'])
        state = self.answer(state, 'example-model')
        return self.answer(state, 'low')
    def test_preview_and_confirm(self):
        state = self.ready(); self.assertEqual(state['step']['id'], 'confirm')
        self.assertFalse(c.path('project', self.project).exists())
        state = self.answer(state, True, apply=True, token=state['step']['preview']['confirmation_token'])
        self.assertEqual(state['status'], 'complete'); self.assertIn('implementer', c.effective(self.project)['roles'])
        self.assertFalse((self.project / '.codex').exists())
    def test_resume(self):
        state = self.answer(self.start(), ['reviewer'])
        resumed = self.start(); self.assertEqual(state, resumed)
        self.assertEqual(resumed['step']['id'], 'reviewer.model')
    def test_stale_step(self):
        state = self.start(); self.answer(state, ['tester'])
        with self.assertRaises(c.Error): self.answer(state, ['reviewer'])
    def test_requires_apply_and_token(self):
        state = self.ready()
        with self.assertRaises(c.Error): self.answer(state, True)
        with self.assertRaises(c.Error): self.answer(state, True, apply=True, token='bad')
        self.assertFalse(c.path('project', self.project).exists())
    def test_changed_config_requires_new_preview(self):
        state = self.ready()
        c.role_write('global', self.project, 'other', dict(c.presets()['tester'], model='x', effort='low'), 'create')
        with self.assertRaises(c.Error): self.answer(state, True, apply=True, token=state['step']['preview']['confirmation_token'])
        current = self.start()
        self.answer(current, True, apply=True, token=current['step']['preview']['confirmation_token'])
    def test_preserve_existing_and_enabled(self):
        c.role_write('global', self.project, 'reviewer', dict(c.presets()['reviewer'], model='old', effort='high'), 'create')
        c.set_enabled('project', self.project, False)
        state = self.answer(self.start(), ['reviewer'])
        self.assertEqual(state['step']['id'], 'confirm')
        self.answer(state, True, apply=True, token=state['step']['preview']['confirmation_token'])
        cfg = c.effective(self.project); self.assertFalse(cfg['enabled']); self.assertEqual(cfg['roles']['reviewer']['model'], 'old')
    def test_cancel_retains_draft(self):
        state = self.answer(self.ready(), False); self.assertEqual(state['status'], 'draft')
        self.assertFalse(c.path('project', self.project).exists())
    def test_models_and_validation(self):
        state = w.advance('project', self.project, models=['custom-a', 'custom-b'])
        state = self.answer(state, ['tester']); self.assertEqual(state['step']['choices'], ['custom-a', 'custom-b'])
        with self.assertRaises(c.Error): self.answer(state, '')
        state = self.answer(state, 'custom-a')
        with self.assertRaises(c.Error): self.answer(state, 'impossible')
    def test_invalid_roles_and_reset(self):
        state = self.start()
        for value in [[], ['missing'], [{}], ['tester', 'tester']]:
            with self.assertRaises(c.Error): self.answer(state, value)
        with self.assertRaises(c.Error): w.advance('project', self.project, reset=True)
        self.assertEqual(w.advance('project', self.project, reset=True, apply=True)['step']['id'], 'roles')
    def test_corrupt_state(self):
        state = self.start(); Path(state['draft_file']).write_text('{}')
        with self.assertRaises(c.Error): self.start()
    def test_global_scope(self):
        state=w.advance('global', self.project)
        for answer in [['tester'],'example','high']:
            state=w.advance('global',self.project,answer,state['step']['id'],state['revision'])
        state=w.advance('global',self.project,True,'confirm',state['revision'],True,state['step']['preview']['confirmation_token'])
        self.assertEqual(state['status'],'complete'); self.assertTrue(c.path('global',self.project).exists())

    def test_non_git_resume_from_child(self):
        (self.project / '.git').rmdir()
        # Seed a valid persisted draft directly so no ancestor test fixture owns the root.
        draft = self.project / '.crew' / 'onboarding.json'
        c.atomic(draft, json.dumps({'schema_version': 1, 'revision': 0, 'language': 'en', 'selected': None, 'roles': {}, 'models': [], 'completed': False}))
        sub = self.project / 'src'; sub.mkdir()
        self.assertEqual(c.project_root(sub), self.project)
        state = w.advance('project', sub)
        self.assertEqual(state['draft_file'], str(draft))
        self.assertEqual(w.advance('project', sub, ['tester'], 'roles', state['revision'])['step']['id'], 'tester.model')
        self.assertEqual(self.start()['step']['id'], 'tester.model')
    def test_reset_rejects_old_answer(self):
        old = self.start()
        w.advance('project', self.project, reset=True, apply=True)
        with self.assertRaises(c.Error): self.answer(old, ['tester'])
