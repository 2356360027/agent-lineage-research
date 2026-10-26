"""Offline dependency closure and refusal tests; never change research evidence."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.prepare_student_club_runtime import closure,prepare,verify,SEEDS


class Tests(unittest.TestCase):
    def setUp(self):
        self.project=Path(__file__).resolve().parents[1]
        temp=tempfile.TemporaryDirectory(prefix='student-bundle-test-',dir=self.project/'runtime/runs')
        self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        for name in ('runtime','scripts'):
            (self.root/name).mkdir();(self.root/name/'__init__.py').write_text('',encoding='utf-8')

    def file(self,name,text):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')

    def test_transitive_from_alias_imports_and_packages(self):
        self.file('scripts/start.py','from runtime import a as renamed\nimport runtime.b\n')
        self.file('runtime/a.py','from runtime.b import value\n')
        self.file('runtime/b.py','import json\nvalue=1\n')
        self.assertEqual(set(closure(self.root,('scripts/start.py',))),{
            'scripts/start.py','scripts/__init__.py','runtime/__init__.py','runtime/a.py','runtime/b.py'})

    def test_missing_module_relative_import_and_traversal_refused(self):
        for text in ('import runtime.missing','from . import sibling'):
            self.file('scripts/start.py',text)
            with self.assertRaises(ValueError):closure(self.root,('scripts/start.py',))
        with self.assertRaises(ValueError):closure(self.root,('../outside.py',))

    def test_real_closure_contains_worker_entrypoints_not_runs_or_user_dirty_module(self):
        files=closure(self.project)
        self.assertTrue(set(SEEDS)<=set(files))
        self.assertIn('runtime/student_club_native_wire.py',files)
        self.assertNotIn('runtime/cache_intent_batch.py',files)
        self.assertTrue(all('/runs/' not in name for name in files))

    def test_existing_output_and_outside_location_refused_before_loading(self):
        with patch('scripts.prepare_student_club_runtime.load') as load:
            with self.assertRaises(ValueError):prepare(self.project,self.root)
            with self.assertRaises(ValueError):prepare(self.project,self.project/'not-private-output')
            load.assert_not_called()

    def test_changed_manifest_refused_before_input_or_counter_loading(self):
        self.file('manifest.json','{}')
        with patch('scripts.prepare_student_club_runtime.load') as load:
            with self.assertRaises(ValueError):verify(self.root,'0'*64)
            load.assert_not_called()


if __name__=='__main__':unittest.main()
