import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('bundle',ROOT/'scripts/build_codex_cli.py')
bundle=importlib.util.module_from_spec(spec);spec.loader.exec_module(bundle)

class CodexCliBundleTests(unittest.TestCase):
    def test_legacy_manifest_routes_to_codex_hooks_and_keeps_portable_backup(self):
        with tempfile.TemporaryDirectory() as d:
            output=bundle.build(ROOT,Path(d)/'crew-cli.zip')
            with zipfile.ZipFile(output) as z:
                names=set(z.namelist())
                self.assertNotIn('crew/plugin.json',names)
                self.assertIn('crew/plugin.portable.json',names)
                manifest=json.loads(z.read('crew/.codex-plugin/plugin.json'))
                portable=json.loads(z.read('crew/plugin.portable.json'))
                self.assertEqual(manifest['version'],portable['version'])
                self.assertEqual(manifest['hooks'],'./hooks/codex.json')
                self.assertIn('crew/crew/workflow_hooks.py',names)
                self.assertFalse(any('/.git/' in n or '__pycache__' in n for n in names))
    def test_existing_output_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'existing.zip';target.write_text('keep')
            with self.assertRaises(ValueError):bundle.build(ROOT,target)
            self.assertEqual(target.read_text(),'keep')

    def test_missing_source_does_not_leave_output_and_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'source';root.mkdir()
            (root/'plugin.json').write_text((ROOT/'plugin.json').read_text())
            output=Path(d)/'retry.zip'
            with patch.object(bundle.subprocess,'check_output',return_value='plugin.json\0missing.py\0'):
                with self.assertRaises(ValueError):bundle.build(root,output)
                self.assertFalse(output.exists())
                (root/'missing.py').write_text('# now available')
                bundle.build(root,output)
            self.assertTrue(zipfile.is_zipfile(output))

    def test_write_failure_leaves_no_output_or_temporary_file(self):
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'broken.zip'
            with patch.object(bundle.zipfile.ZipFile,'write',side_effect=OSError('simulated read failure')):
                with self.assertRaises(OSError):bundle.build(ROOT,output)
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(d).iterdir()),[])

    def test_publication_race_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'race.zip'
            def conflict(source,target):
                Path(target).write_text('another writer')
                raise FileExistsError('concurrent output')
            with patch.object(bundle.os,'link',side_effect=conflict):
                with self.assertRaises(FileExistsError):bundle.build(ROOT,output)
            self.assertEqual(output.read_text(),'another writer')
            self.assertEqual(list(Path(d).iterdir()),[output])
