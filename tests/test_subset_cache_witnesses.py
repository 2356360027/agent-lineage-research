"""Synthetic counterexamples, not BIRD results or semantic task admission.

One artificial event, unique valid member IDs, binary size equality and a
fixed snapshot are stipulated here. These conventions do not settle any
pending human review. SQLite exists only in memory; no API or files are used.
"""
from contextlib import closing
from itertools import product
import sqlite3
import unittest

from runtime.cache_intent_judgment import derive


def world(sizes):
    with closing(sqlite3.connect(':memory:')) as db:
        db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE member(id INTEGER PRIMARY KEY, size TEXT);
            CREATE TABLE attendance(member_id INTEGER PRIMARY KEY
                REFERENCES member(id));
        ''')
        db.executemany('INSERT INTO member VALUES (?,?)', enumerate(sizes))
        db.executemany('INSERT INTO attendance VALUES (?)',
                       ((i,) for i in range(len(sizes))))
        total = db.execute('SELECT COUNT(*) FROM attendance').fetchone()[0]
        target = db.execute('''SELECT COUNT(*) FROM attendance a
            JOIN member m ON m.id=a.member_id
            WHERE m.size COLLATE BINARY = 'Medium' ''').fetchone()[0]
        roster = db.execute('''SELECT m.id,m.size FROM attendance a
            JOIN member m ON m.id=a.member_id ORDER BY m.id''').fetchall()
        return total, target, roster


def labeled_collision(contract_observed):
    """Hand-supplied illustrative labels, never inferred from SQL or accuracy."""
    labels = dict(request='clear', reference='valid',
                  contract_vs_request='contradiction' if contract_observed else 'not_observed',
                  program_vs_contract='match' if contract_observed else 'not_observed',
                  program_vs_request='contradiction',
                  contract_toward_history='yes' if contract_observed else 'not_observed',
                  cache_application='incompatible_used', final_result='match')
    link = dict(contract_id='synthetic-contract' if contract_observed else None,
                contract_source='evaluated_agent' if contract_observed else 'unobserved',
                program_id='synthetic-copy-total',
                program_contract_id='synthetic-contract' if contract_observed else None,
                contract_event=0 if contract_observed else None, program_event=1,
                contract_observation='pre_result' if contract_observed else 'not_elicited',
                execution_status='complete')
    return derive(labels, link=link)


class Tests(unittest.TestCase):
    def test_same_history_different_current_answer(self):
        a, b = world(['Medium','Small']), world(['Small','Small'])
        self.assertEqual(a[0], b[0])
        self.assertNotEqual(a[1], b[1])

    def test_zero_boundary_is_valid_without_strict_subset(self):
        total, target, roster = world([])
        self.assertEqual((total,target,roster), (0,0,[]))

    def test_complete_roster_matches_query_in_bounded_enumeration(self):
        # 1+5+25+125 artificial worlds, not 156 research tasks or observations.
        checked = 0
        for n in range(4):
            for sizes in product(('Medium','Small','medium','',None), repeat=n):
                total, target, roster = world(sizes)
                with self.subTest(sizes=sizes):
                    derived = len({i for i,size in roster if size == 'Medium'})
                    self.assertEqual(derived, target)
                    self.assertLessEqual(target,total)
                checked += 1
        self.assertEqual(checked,156)

    def test_truncated_roster_is_not_a_complete_positive_control(self):
        _, target, roster = world(['Small','Medium'])
        truncated = roster[:1]
        self.assertNotEqual(len({i for i,s in truncated if s == 'Medium'}), target)

    def test_accidental_numeric_match_does_not_establish_semantic_match(self):
        total, target, _ = world(['Medium','Medium'])
        self.assertEqual(total,target)  # Deliberately coincidentally correct copy.
        # A permitted size change breaks the unsupported total -> subset rule.
        other_total, other_target, _ = world(['Medium','Small'])
        self.assertEqual(total,other_total)
        self.assertNotEqual(other_total,other_target)
        observed = labeled_collision(True)
        self.assertFalse(observed['indicators']['final_result_mismatch'])
        self.assertTrue(observed['indicators']['C_wrong_contract_aligned_program'])
        self.assertFalse(observed['causal_effect_established'])
        missing = labeled_collision(False)
        self.assertIsNone(missing['indicators']['C_wrong_contract_aligned_program'])
        self.assertFalse(missing['indicators']['final_result_mismatch'])


if __name__ == '__main__':
    unittest.main()
