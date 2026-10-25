"""Synthetic SQL/serialization checks, not research observations."""
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from scripts.audit_student_club_structure import QUERIES, integers
from runtime import bird_readonly_audit as bird


class Tests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1]/'runtime/runs'
        self.temp = tempfile.TemporaryDirectory(prefix='student-structure-test-', dir=base)
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name)/'synthetic.sqlite'
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript('CREATE TABLE event(event_id TEXT,event_name TEXT,event_date TEXT);'
                'CREATE TABLE attendance(link_to_event TEXT,link_to_member TEXT);'
                'CREATE TABLE member(member_id TEXT,t_shirt_size);')

    def query(self, name):
        before = bird.hash_file(self.db)
        result = bird.execute_readonly(self.db, QUERIES[name])
        self.assertEqual(before, bird.hash_file(self.db))
        self.assertTrue(result['complete'], result)
        # Audit must never inspect individual names, contact information or size values.
        forbidden = {'first_name','last_name','email','phone','zip'}
        self.assertFalse(forbidden & {column for _,column in result['read_columns']})
        return integers(result)

    def test_empty(self):
        for name in QUERIES:
            out = self.query(name)
            self.assertTrue(all(v in (0,None) for v in out.values()), out)

    def test_duplicates_orphans_and_size_quality(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.executemany('INSERT INTO event VALUES (?,?,?)', [
                ('e1',"Women's Soccer",'d1'),('e2',"Women's Soccer",'d2'),
                ('e3',"women's soccer",'d3')])
            c.executemany('INSERT INTO member VALUES (?,?)', [('m1','Medium'),('m2',None),('m3',''),('m4',17)])
            c.executemany('INSERT INTO attendance VALUES (?,?)', [
                ('e1','m1'),('e1','m1'),('e2','m1'),('e1','m2'),('e1','m3'),('e1','m4'),
                ('e1','missing'),('e1',None),('e3','m1')])
            c.commit()
        self.assertEqual(self.query('event_scope')['event_rows'],2)
        self.assertEqual(self.query('attendance_scope'), dict(attendance_rows=8,nonnull_member_links=7,distinct_member_links=5))
        self.assertEqual(self.query('repeated_pairs')['repeated_event_member_pairs'],1)
        self.assertEqual(self.query('target_link_integrity'),dict(rows_checked=8,null_links=1,orphan_links=1))
        self.assertEqual(self.query('target_size_quality'),dict(joined_rows=6,null_sizes=1,empty_sizes=1,nontext_sizes=1))

    def test_incomplete_or_unexpected_output_rejected(self):
        with self.assertRaises(ValueError):
            integers({'complete':False,'rows':[]})
        with self.assertRaises(ValueError):
            integers({'complete':True,'columns':['x'],'rows':[[['text','private']]]})


if __name__ == '__main__':
    unittest.main()
