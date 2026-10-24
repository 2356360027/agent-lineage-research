import unittest
from copy import deepcopy
from runtime.cache_action_factorial_capacity import FactorialCapacity, validate_cell
from runtime.cache_action_factorial_preview import make_cells
from runtime.cache_action_preview import payload


class Counter:
    # Test stand-in only; actual preparation uses hash-verified reference tokenizer.
    def chat(self, messages): return 100
    def text(self, text): return len(text)


class Tests(unittest.TestCase):
    def cells(self):
        return make_cells('Count entries.',None,['CREATE TABLE t(id INTEGER)'],
                          dict(historical_request='List entries.',evidence=None,sql='SELECT id FROM t'),'a'*64)

    def test_all_cells_have_initial_payload_and_four_generation_bounds(self):
        for cell in self.cells():
            c = FactorialCapacity(cell,Counter())
            self.assertEqual(c(payload(cell['messages']))['generation_index'],1)
            certificate = c.certificate()
            self.assertEqual(certificate['max_generations'],4)
            self.assertEqual(certificate['total_output_bound'],2048)
            self.assertFalse(certificate['inference_authorized'])

    def test_changed_input_not_accepted(self):
        cell = deepcopy(self.cells()[0]); cell['messages'][0]['content'] += 'Changed'
        with self.assertRaises(ValueError): validate_cell(cell)

    def test_cross_cell_payload_rejected(self):
        a,b,*_ = self.cells()
        with self.assertRaises(ValueError): FactorialCapacity(a,Counter())(payload(b['messages']))

    def test_unbounded_generation_setting_rejected(self):
        cell = self.cells()[0]; p = payload(cell['messages']); p['max_tokens'] = 1000
        with self.assertRaises(ValueError): FactorialCapacity(cell,Counter())(p)

    def test_oversize_initial_stops(self):
        class TooBig(Counter):
            def chat(self, messages): return 32768
        with self.assertRaises(ValueError): FactorialCapacity(self.cells()[0],TooBig())


if __name__ == '__main__': unittest.main()
