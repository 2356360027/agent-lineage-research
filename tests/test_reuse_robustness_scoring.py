import unittest
from scripts.score_reuse_robustness_v2 import UsageCompatibleCounter


class Tests(unittest.TestCase):
    def test_nested_usage_not_added(self):
        c = UsageCompatibleCounter()
        for _ in range(2):
            c.update({'prompt_tokens': 4, 'completion_tokens': 2, 'total_tokens': 6,
                      'prompt_tokens_details': {'cached_tokens': 0}})
        self.assertEqual(dict(c), {'prompt_tokens': 8, 'completion_tokens': 4, 'total_tokens': 12})

    def test_label_counts_unchanged(self):
        self.assertEqual(dict(UsageCompatibleCounter(['applicable', 'invalid', 'applicable'])),
                         {'applicable': 2, 'invalid': 1})

    def test_invalid_usage_fails(self):
        with self.assertRaises(ValueError):
            UsageCompatibleCounter({'prompt_tokens': 4, 'completion_tokens': 2, 'total_tokens': 5})


if __name__ == '__main__': unittest.main()
