import sqlite3
import unittest
from scripts.check_cache_information_loss import fixtures
from scripts.check_cache_full_schema import evaluate_full, SCHEMA_QUERY


def schema(ddl):
    con = sqlite3.connect(':memory:')
    try:
        con.executescript(ddl)
        return con.execute(SCHEMA_QUERY).fetchall()
    finally:
        con.close()


class Tests(unittest.TestCase):
    def test_optional_columns_and_unique_index_recreated(self):
        ddl, variants = fixtures((537, 555))
        full = schema(ddl+' CREATE UNIQUE INDEX by_id ON posts(Id);')
        result = evaluate_full(full, variants[0], 'SELECT COUNT(*) FROM posts', 'SELECT AVG(Score) FROM posts')
        self.assertTrue(result['schema_exact'])
        self.assertEqual(result['current']['rows'], [(3.0,)])

    def test_no_ignoring_name_unique_constraint(self):
        ddl, variants = fixtures((766, 786))
        full = schema(ddl+' CREATE UNIQUE INDEX no_duplicate_name ON superhero(full_name);')
        with self.assertRaises(sqlite3.IntegrityError):
            evaluate_full(full, variants[1], 'SELECT DISTINCT full_name FROM superhero', 'SELECT COUNT(*) FROM superhero')

    def test_no_ignoring_required_fields(self):
        ddl, variants = fixtures((537, 555))
        ddl = ddl.replace('DisplayName TEXT', 'DisplayName TEXT, required_field TEXT NOT NULL')
        with self.assertRaises(sqlite3.IntegrityError):
            evaluate_full(schema(ddl), variants[0], 'SELECT COUNT(*) FROM posts', 'SELECT AVG(Score) FROM posts')

    def test_no_ignoring_foreign_keys(self):
        ddl, variants = fixtures((537, 555))
        with self.assertRaises(sqlite3.IntegrityError):
            evaluate_full(schema(ddl), variants[0][1:], 'SELECT COUNT(*) FROM posts', 'SELECT AVG(Score) FROM posts')


if __name__ == '__main__':
    unittest.main()
