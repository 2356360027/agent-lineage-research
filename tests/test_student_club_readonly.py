"""Synthetic privacy and cost tests, never real personal records or API calls."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from runtime import student_club_readonly as scoped
from runtime import bird_readonly_audit as audit
from runtime.cache_intent_journal import verify
from scripts.check_student_club_reference import ENTITY_H, ENTITY_C


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='student-scope-',
            dir=Path(__file__).resolve().parents[1]/'runtime/runs')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=self.root/'synthetic.sqlite'
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript('''
                CREATE TABLE event(event_id TEXT PRIMARY KEY,event_name TEXT,event_date TEXT,notes TEXT);
                CREATE TABLE member(member_id TEXT PRIMARY KEY,t_shirt_size TEXT,email TEXT,phone TEXT,first_name TEXT);
                CREATE TABLE attendance(link_to_event TEXT,link_to_member TEXT);
                CREATE TABLE unrelated(secret TEXT);
                CREATE VIEW hidden_contact AS SELECT email FROM member;
                INSERT INTO event VALUES ('e1','Women''s Soccer','d1','PRIVATE_NOTE_SENTINEL');
                INSERT INTO member VALUES ('m1','Medium','PRIVATE_EMAIL_SENTINEL','PRIVATE_PHONE_SENTINEL','PRIVATE_NAME_SENTINEL');
                INSERT INTO member VALUES ('m2','Small','PRIVATE_EMAIL_SENTINEL','PRIVATE_PHONE_SENTINEL','PRIVATE_NAME_SENTINEL');
                INSERT INTO attendance VALUES ('e1','m1'),('e1','m2');
                INSERT INTO unrelated VALUES ('PRIVATE_OTHER_SENTINEL');
            ''')
        self.before=audit.hash_file(self.db)

    def check_query(self,sql):
        out=scoped.execute(self.db,sql)
        self.assertEqual(audit.hash_file(self.db),self.before)
        self.assertEqual(out['column_policy_version'],scoped.VERSION)
        return out

    def test_correct_count_and_projected_reads_remain_possible(self):
        for sql,expected in ((ENTITY_H,'2'),(ENTITY_C,'1'),('SELECT COUNT(*) FROM member','2')):
            out=self.check_query(sql)
            self.assertTrue(out['complete'],out)
            self.assertEqual(out['rows'],[[['integer',expected]]])
        out=self.check_query('SELECT member_id,t_shirt_size FROM member ORDER BY member_id')
        self.assertTrue(out['complete'])
        self.assertEqual(out['retained_rows'],2)

    def test_disallowed_columns_denied_before_any_result(self):
        queries=(
            'SELECT email FROM member','SELECT * FROM member','SELECT rowid FROM member',
            'SELECT upper(email) FROM member','SELECT COUNT(email) FROM member',
            "SELECT COUNT(*) FROM member WHERE email LIKE '%x%'",
            'SELECT COUNT(*) FROM member GROUP BY phone',
            'SELECT member_id FROM member ORDER BY first_name',
            'SELECT member_id FROM member WHERE EXISTS (SELECT email FROM member)',
            'WITH x AS (SELECT email FROM member) SELECT * FROM x',
            'SELECT email FROM hidden_contact','SELECT notes FROM event',
            'SELECT COUNT(*) FROM unrelated','SELECT name FROM sqlite_schema',
            'SELECT member_id FROM member UNION ALL SELECT email FROM member',
            'SELECT email FROM member WHERE 0',
        )
        for sql in queries:
            with self.subTest(sql=sql):
                out=self.check_query(sql)
                self.assertFalse(out['complete'])
                self.assertEqual(out['status'],'DENIED')
                self.assertEqual(out['rows'],[])
                self.assertNotIn('PRIVATE_',str(out))

    def test_writes_and_policy_escape_rejected(self):
        for sql in ('DELETE FROM member','PRAGMA table_info(member)',
                    "ATTACH DATABASE ':memory:' AS x","SELECT load_extension('x')"):
            self.assertFalse(self.check_query(sql)['complete'])
        for bad in ('all', {'member':'email'}, {'member':['']}, {'member':[None]}):
            with self.assertRaises(ValueError):
                audit.execute_readonly(self.db,'SELECT 1',allowed_columns=bad)
        self.assertFalse(audit.execute_readonly(self.db,'SELECT COUNT(*) FROM member',allowed_columns={})['complete'])

    def test_legacy_default_preserved_on_synthetic_data(self):
        self.assertTrue(audit.execute_readonly(self.db,'SELECT email FROM member')['complete'])
        self.assertFalse(self.check_query('SELECT email FROM member')['complete'])

    def test_scoped_child_and_policy_receipt(self):
        result=scoped.run_child(self.db,ENTITY_C)
        self.assertEqual(result['rows'],[[['integer','1']]])
        self.assertEqual(result['column_policy_sha256'],audit.digest(scoped.policy()))

    def test_session_denial_charged_and_correct_recompute_available_all_cells(self):
        history=ENTITY_H
        cached=scoped.execute(self.db,history)
        cached['sql_sha256']=audit.digest(history)
        for a,p in ((False,1),(False,20),(True,1),(True,20)):
            session=scoped.ToolSession(database_path=self.db,database_sha256=self.before,
                history_sql=history,cached_result=cached,input_receipt={'synthetic':True},
                output=self.root/f'a{a}-p{p}',materialized_available=a,cache_read_cost=p)
            denied=session.call('run_sql',{'sql':'SELECT email FROM member'})
            self.assertEqual(denied['status'],'DENIED')
            self.assertEqual(denied['charged_units'],10)
            self.assertNotIn('PRIVATE_',str(denied))
            answer=session.call('run_sql',{'sql':ENTITY_C})
            self.assertEqual(answer['rows'],[[['integer','1']]])
            self.assertEqual(answer['charged_units'],10)
            cache=session.call('read_cache',{'cache_id':'v000'})
            self.assertEqual(cache['status'],'EXECUTED' if a else 'CACHE_UNAVAILABLE')
            if not a:self.assertNotIn('rows',cache)
            closed=session.close()
            self.assertEqual(closed['status'],'TOOL_SESSION_CLOSED')
            self.assertEqual(closed['spent_declared_units'],20+(p if a else 0))
            verify(session.output)


if __name__=='__main__':unittest.main()
