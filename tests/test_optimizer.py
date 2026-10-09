import glob
import os
import unittest

from execution.interpreter import run
from execution.validator import Validator, generate_test_inputs
from frontend.tac import compile_source, parse_tac
from optimizer.cost import cost, measure
from optimizer.mutations import OPERATORS, UNSAFE_OPERATORS
from optimizer.search import optimize

BENCH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "benchmarks")


class ValidatorTests(unittest.TestCase):
    def test_rejects_wrong_candidate(self):
        original = parse_tac("input a\ninput b\nt1 = a - b\nprint t1")
        wrong = parse_tac("input a\ninput b\nt1 = b - a\nprint t1")
        valid, _ = Validator(original).check(wrong)
        self.assertFalse(valid)

    def test_accepts_equivalent_candidate(self):
        original = parse_tac("input a\nt1 = a * 2\nprint t1")
        same = parse_tac("input a\nt1 = a + a\nprint t1")
        valid, _ = Validator(original).check(same)
        self.assertTrue(valid)

    def test_inputs_deterministic(self):
        self.assertEqual(generate_test_inputs(3, seed=1), generate_test_inputs(3, seed=1))


class CostTests(unittest.TestCase):
    def test_original_cost_is_one(self):
        program = compile_source("input a; print a * 2 + 1;")
        validator = Validator(program)
        m = measure(program, validator.reference)
        self.assertAlmostEqual(cost(m, m), 1.0)

    def test_stall_cycles(self):
        dependent = parse_tac("input a\nt1 = a * 3\nt2 = t1 + 1\nprint t2")
        # input, mul (+stall on a), add (+stall on t1), print (+stall on t2)
        self.assertEqual(run(dependent, [1]).cycles, 1 + (3 + 1) + (1 + 1) + (1 + 1))
        independent = parse_tac("input a\ninput b\nt1 = a * 3\nprint b")
        self.assertEqual(run(independent, [1, 2]).cycles, 1 + 1 + 3 + 1)


class SearchTests(unittest.TestCase):
    def test_benchmarks_improve_and_stay_correct(self):
        files = sorted(glob.glob(os.path.join(BENCH_DIR, "*.src")))
        self.assertTrue(files)
        for path in files:
            with self.subTest(benchmark=os.path.basename(path)):
                with open(path) as fh:
                    program = compile_source(fh.read())
                res = optimize(program)
                self.assertLess(res.final_cost, 1.0)
                costs = res.cost_history
                self.assertTrue(all(b < a for a, b in zip(costs, costs[1:])), "cost must strictly decrease")
                # independent check on fresh inputs the search never saw
                for case in generate_test_inputs(sum(i.op == "input" for i in program), 40, seed=999):
                    self.assertEqual(run(program, case).observable, run(res.optimized, case).observable)

    def test_unsafe_operator_rejected_and_result_still_correct(self):
        program = compile_source("input a, b; x = a - b; y = x * 3; print y; print x;")
        res = optimize(program, OPERATORS + UNSAFE_OPERATORS)
        self.assertGreater(res.rejected_by_op["MX"], 0)
        for case in generate_test_inputs(2, 40, seed=7):
            self.assertEqual(run(program, case).observable, run(res.optimized, case).observable)

    def test_random_programs_fuzz(self):
        from tests.fuzz import fuzz
        unsound, wrong = fuzz(n=40, seed=3, verbose=False)
        self.assertEqual(wrong, [], "optimised program disagrees with AST oracle")
        self.assertEqual(unsound, [], "a safe operator proposed an invalid candidate")

    def test_fully_constant_program(self):
        res = optimize(compile_source("x = 2 * 3 + 4; y = x * 1 + 0; print y;"))
        self.assertEqual([str(i) for i in res.optimized], ["print 10"])


if __name__ == "__main__":
    unittest.main()
