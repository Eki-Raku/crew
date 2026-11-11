import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from crew import core as c
from crew import migration as m


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'; self.project.mkdir(); (self.project / '.git').mkdir()
        self.env = patch.dict(os.environ, {'CREW_HOST': 'codex', 'CODEX_HOME': str(self.root / 'home')}); self.env.start()
        self.role = {'model': 'example', 'effort': 'low', 'description': 'Example', 'instructions': ''}
    def tearDown(self): self.env.stop(); self.tmp.cleanup()
    def legacy(self, scope='global', **extra):
        target = (c.home() / 'rolepilot' if scope == 'global' else self.project / '.rolepilot') / 'config.json'
        c.save(target, dict(schema_version=1, roles={'example': self.role}, **extra))
        return target
    def test_preview_no_writes(self):
        old = self.legacy(); before = old.read_bytes()
        result = m.migrate(self.project)
        self.assertEqual(result['configurations'][0]['add_roles'], ['example'])
        self.assertFalse((c.home() / 'crew').exists()); self.assertEqual(old.read_bytes(), before)
    def test_apply_idempotent_preserves_legacy(self):
        old = self.legacy(enabled=False); before = old.read_bytes()
        m.migrate(self.project, apply=True)
        cfg = c.read(c.home() / 'crew/config.json')
        self.assertEqual(cfg['roles']['example']['instructions'], ''); self.assertFalse(cfg['enabled'])
        result = m.migrate(self.project, apply=True)
        self.assertTrue(all(not row['changed'] for row in result['configurations']))
        self.assertEqual(old.read_bytes(), before)
    def test_conflict_preserves_crew(self):
        self.legacy(enabled=False)
        role = dict(self.role, instructions='My edited prompt')
        c.save(c.home() / 'crew/config.json', {'schema_version': 1, 'roles': {'example': role}, 'enabled': True})
        result = m.migrate(self.project, apply=True)['configurations'][0]
        self.assertEqual(result['conflicts'], ['example']); self.assertTrue(result['enabled_conflict'])
        cfg = c.read(c.home() / 'crew/config.json')
        self.assertEqual(cfg['roles']['example'], role); self.assertTrue(cfg['enabled'])
    def test_project_and_global_stay_separate(self):
        self.legacy('global'); self.legacy('project', enabled=False)
        m.migrate(self.project, apply=True)
        self.assertFalse(c.read(self.project / '.crew/config.json')['enabled'])
        self.assertNotIn('enabled', c.read(c.home() / 'crew/config.json'))
    def test_legacy_only_root(self):
        self.legacy('project'); (self.project / '.git').rmdir(); sub = self.project / 'src'; sub.mkdir()
        self.assertEqual(m.migration_root(sub), self.project)
        m.migrate(sub, 'project', True)
        self.assertTrue((self.project / '.crew/config.json').exists())
    def test_symlink_refused(self):
        self.legacy(); elsewhere = self.root / 'elsewhere'; elsewhere.mkdir()
        (c.home() / 'crew').symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(c.Error): m.migrate(self.project, apply=True)
        self.assertFalse(list(elsewhere.iterdir()))
    def test_inventory_legacy_files_preserved(self):
        self.legacy(); directory = self.project / '.codex/agents'; directory.mkdir(parents=True)
        old = directory / 'rolepilot-example.toml'; old.write_text('# Managed by RolePilot.\nname = "rolepilot_example"')
        entry = c.home() / 'AGENTS.md'; entry.write_text('User text\n<!-- rolepilot:start -->\nold\n<!-- rolepilot:end -->\n')
        result = m.migrate(self.project, apply=True)
        self.assertTrue(result['legacy_exports'][0]['managed'])
        self.assertEqual(result['legacy_agents_entries'], [str(entry)])
        self.assertTrue(old.exists()); self.assertIn('User text', entry.read_text())
    def test_invalid_source_no_partial_write(self):
        self.legacy(); bad = self.project / '.rolepilot/config.json'; bad.parent.mkdir(); bad.write_text('{bad')
        with self.assertRaises(c.Error): m.migrate(self.project, apply=True)
        self.assertFalse((c.home() / 'crew/config.json').exists())

if __name__ == '__main__': unittest.main()
