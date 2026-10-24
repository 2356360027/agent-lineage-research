from copy import deepcopy
import unittest
from runtime import bird_readonly_audit as audit
from scripts.prepare_factorial_binding import bind_history
from tests.test_factorial_loop import cells


class Tests(unittest.TestCase):
    def setUp(self):
        rows=[[['integer','1']]]
        self.original=dict(status='EXECUTED',complete=True,columns=['id'],rows=rows,**audit.fingerprints(rows))

    def test_only_sql_binding_added_original_not_modified(self):
        before=deepcopy(self.original)
        bound=bind_history(self.original,'SELECT id FROM fixture',cells())
        self.assertEqual(self.original,before)
        self.assertEqual(bound.pop('sql_sha256'),audit.digest('SELECT id FROM fixture'))
        self.assertEqual(bound,before)

    def test_different_execution_sql_or_modified_rows_rejected(self):
        with self.assertRaises(ValueError): bind_history(self.original,'SELECT 2',cells())
        self.original['rows']=[[['integer','2']]]
        with self.assertRaises(ValueError): bind_history(self.original,'SELECT id FROM fixture',cells())

    def test_incomplete_or_rebound_cache_rejected(self):
        self.original['complete']=False
        with self.assertRaises(ValueError): bind_history(self.original,'SELECT id FROM fixture',cells())
        self.original['complete']=True; self.original['sql_sha256']='a'*64
        with self.assertRaises(ValueError): bind_history(self.original,'SELECT id FROM fixture',cells())


if __name__=='__main__': unittest.main()
