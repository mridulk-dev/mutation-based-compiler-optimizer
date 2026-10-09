import unittest

from frontend.tac import format_tac, parse_tac
from optimizer import mutations as m


def tac(text):
    return parse_tac(text)


def results(fn, text):
    return [format_tac(mut.program, numbered=False) for mut in fn(tac(text))]


class MutationOperatorTests(unittest.TestCase):
    def test_m1_zero_add(self):
        self.assertEqual(results(m.zero_add_removal, "input a\nt1 = a + 0\nt2 = 0 + a\nt3 = a - 0\nt4 = 0 - a"),
                         ["input a\nt1 = a\nt2 = 0 + a\nt3 = a - 0\nt4 = 0 - a",
                          "input a\nt1 = a + 0\nt2 = a\nt3 = a - 0\nt4 = 0 - a",
                          "input a\nt1 = a + 0\nt2 = 0 + a\nt3 = a\nt4 = 0 - a"])  # 0 - a is NOT a

    def test_m2_one_mul(self):
        out = results(m.one_mul_removal, "input a\nt1 = a * 1\nt2 = 1 / a")
        self.assertEqual(out, ["input a\nt1 = a\nt2 = 1 / a"])  # 1 / a is NOT a

    def test_m3_constant_folding_skips_division_by_zero(self):
        out = results(m.constant_folding, "t1 = 7 / 2\nt2 = 5 / 0\nt3 = -7 % 2")
        self.assertEqual(out, ["t1 = 3\nt2 = 5 / 0\nt3 = -7 % 2", "t1 = 7 / 2\nt2 = 5 / 0\nt3 = -1"])

    def test_m4_strength_reduction(self):
        out = results(m.strength_reduction, "input a\nt1 = a * 2\nt2 = 8 * a\nt3 = a * 6")
        self.assertEqual(out, ["input a\nt1 = a + a\nt2 = 8 * a\nt3 = a * 6",
                               "input a\nt1 = a * 2\nt2 = a << 3\nt3 = a * 6"])

    def test_m5_dead_assignment(self):
        out = results(m.dead_assignment_removal, "input a\nx = a + 1\nx = a + 2\ny = a * 3\nprint x")
        self.assertEqual(sorted(out), sorted(["input a\nx = a + 2\ny = a * 3\nprint x",
                                              "input a\nx = a + 1\nx = a + 2\nprint x"]))

    def test_m5_never_removes_input(self):
        self.assertEqual(results(m.dead_assignment_removal, "input a\ninput b\nprint b"), [])

    def test_m5_keeps_dead_division_that_may_trap(self):
        # regression (found by fuzzing): removing it hides a division-by-zero error
        self.assertEqual(results(m.dead_assignment_removal, "input a\nx = 5 / a\nprint a"), [])
        self.assertEqual(results(m.dead_assignment_removal, "input a\nx = a / 5\nprint a"),
                         ["input a\nprint a"])  # constant non-zero divisor cannot trap

    def test_m8_never_moves_trapping_op_across_print(self):
        # regression (found by fuzzing): would change how many values print before the error
        self.assertEqual(results(m.instruction_reordering, "input a\nprint a\nt1 = 5 % a"), [])

    def test_m6_cse_commutative(self):
        out = results(m.common_subexpression_elimination, "input a\ninput b\nt1 = a + b\nt2 = b + a\nprint t2")
        self.assertEqual(out, ["input a\ninput b\nt1 = a + b\nt2 = t1\nprint t2"])

    def test_m6_cse_respects_redefinition(self):
        text = "input a\ninput b\nt1 = a - b\na = 5\nt2 = a - b\nprint t2"
        self.assertEqual(results(m.common_subexpression_elimination, text), [])

    def test_m7_constant_propagation_removes_dead_copy(self):
        out = results(m.constant_propagation, "input a\nk = 4\nt1 = a * k\nprint t1")
        self.assertEqual(out, ["input a\nt1 = a * 4\nprint t1"])

    def test_m7_stops_at_redefinition(self):
        out = results(m.constant_propagation, "input a\nk = 4\nt1 = a * k\nk = a\nprint k")
        self.assertEqual(out, ["input a\nt1 = a * 4\nk = a\nprint k"])

    def test_m8_reorder_only_independent(self):
        out = results(m.instruction_reordering, "input a\nt1 = a * 3\nt2 = a + 1\nt3 = t1 + t2\nprint t3")
        self.assertEqual(out, ["input a\nt2 = a + 1\nt1 = a * 3\nt3 = t1 + t2\nprint t3"])

    def test_m8_keeps_io_order(self):
        self.assertEqual(results(m.instruction_reordering, "input a\ninput b\nprint a\nprint b"), [])


if __name__ == "__main__":
    unittest.main()
