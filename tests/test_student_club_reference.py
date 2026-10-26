"""Synthetic checks only; no real dataset or API access."""
from contextlib import closing
import sqlite3
import unittest
from scripts.check_student_club_reference import ENTITY_H, ENTITY_C, scalar


class Tests(unittest.TestCase):
    def test_scalar_fail_closed(self):
        def response(value):
            return dict(complete=True,status='EXECUTED',columns=['count'],rows=[[value]])
        self.assertEqual(scalar(response(['integer','0'])),0)
        self.assertEqual(scalar(response(['integer','12'])),12)
        for value in (['null',None],['real','0x0.0p+0'],['text','1'],['integer','-1'],['integer',True]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                scalar(response(value))
        for invalid in ({},response(['integer','1'])|{'complete':False},
                        response(['integer','1'])|{'rows':[]},
                        response(['integer','1'])|{'rows':[[['integer','1'],['integer','2']]]}):
            with self.assertRaises(ValueError): scalar(invalid)

    def test_entity_count_empty_and_edge_cases(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('CREATE TABLE member(member_id TEXT,t_shirt_size TEXT);'
                'CREATE TABLE event(event_id TEXT,event_name TEXT);'
                'CREATE TABLE attendance(link_to_event TEXT,link_to_member TEXT);')
            self.assertEqual(db.execute(ENTITY_H).fetchone(),(0,))
            self.assertEqual(db.execute(ENTITY_C).fetchone(),(0,))
            db.executemany('INSERT INTO member VALUES (?,?)',
                [('m1','Medium'),('m2','medium'),('m3',None),('m4','Medium')])
            db.executemany('INSERT INTO event VALUES (?,?)',
                [('e1',"Women's Soccer"),('e2',"Women's Soccer"),('e3',"women's soccer")])
            db.executemany('INSERT INTO attendance VALUES (?,?)',
                [('e1','m1'),('e1','m1'),('e2','m1'),('e1','m2'),('e1','m3'),
                 ('e1','missing'),('e1',None),('e3','m4')])
            self.assertEqual(db.execute(ENTITY_H).fetchone(),(3,))
            self.assertEqual(db.execute(ENTITY_C).fetchone(),(1,))


if __name__=='__main__': unittest.main()
