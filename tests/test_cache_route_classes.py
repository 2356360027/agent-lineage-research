import unittest
from runtime.bird_readonly_audit import fingerprints
from scripts.audit_cache_route_classes import filter_project, count_distinct


def fixture(columns, rows):
    return dict(status='EXECUTED', complete=True, columns=columns, rows=rows, **fingerprints(rows))


class RouteTests(unittest.TestCase):
    def test_filter_projection_and_dedup(self):
        cache = fixture(['molecule_id','label'], [
            [['text','a'],['text','+']], [['text','a'],['text','+']],
            [['text','b'],['text','-']], [['text','c'],['null',None]]])
        self.assertEqual(filter_project(cache), [[['text','a']]])

    def test_count_dedup_and_null(self):
        cache = fixture(['bond_id'], [[['text','b']], [['text','b']], [['null',None]], [['text','c']]])
        self.assertEqual(count_distinct(cache), [[['integer','2']]])

    def test_wrong_columns_fail_closed(self):
        with self.assertRaises(ValueError):
            count_distinct(fixture(['other'], []))

    def test_tampered_rows_fail(self):
        cache = fixture(['bond_id'], [[['text','b']]])
        cache['rows'] = []
        with self.assertRaises(ValueError):
            count_distinct(cache)
