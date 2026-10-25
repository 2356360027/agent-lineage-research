import unittest
from scripts.check_cache_information_loss import witness, fixtures, run
from unittest.mock import patch
from pathlib import Path


class Tests(unittest.TestCase):
    def test_count_not_mean(self):
        result = witness('SELECT COUNT(*) FROM posts', 'SELECT AVG(Score) FROM posts', (537, 555))
        self.assertTrue(result['is_information_loss_witness'])
        self.assertEqual([r['current']['rows'] for r in result['results']], [[(3.0,)], [(5.0,)]])

    def test_name_not_identity(self):
        result = witness('SELECT DISTINCT full_name FROM superhero',
                         'SELECT COUNT(DISTINCT id) FROM superhero', (766, 786))
        self.assertTrue(result['is_information_loss_witness'])
        self.assertEqual([r['current']['rows'] for r in result['results']], [[(1,)], [(2,)]])

    def test_sufficient_result_not_witness(self):
        result = witness('SELECT AVG(Score) FROM posts', 'SELECT AVG(Score) FROM posts', (537, 555))
        self.assertFalse(result['is_information_loss_witness'])

    def test_unchanged_current_not_witness(self):
        result = witness('SELECT COUNT(*) FROM posts', 'SELECT COUNT(*) FROM posts', (537, 555))
        self.assertFalse(result['is_information_loss_witness'])

    def test_unknown_pair(self):
        with self.assertRaises(ValueError):
            fixtures((1, 2))

    def test_wrong_source_refused_before_output(self):
        # No temporary filesystem access is needed to exercise this early gate.
        with patch('scripts.check_cache_information_loss.sha', return_value='wrong'), \
                patch.object(Path, 'mkdir') as mkdir:
            with self.assertRaises(ValueError):
                run(Path('unused-source'), Path('unused-output'))
            mkdir.assert_not_called()


if __name__ == '__main__':
    unittest.main()
