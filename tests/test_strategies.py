import json
import os
import unittest

from execution.interpreter import run
from execution.pycodegen import compile_tac
from execution.timing import summarize, time_variants
from execution.validator import generate_test_inputs
from frontend.tac import compile_source
from optimizer.cost import WEIGHT_CONFIGS, cost, measure
from optimizer.mutations import OPERATORS, UNSAFE_OPERATORS
from optimizer.strategies import FIXED_ORDER, STRATEGIES, Evaluator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def review1_programs():
    with open(os.path.join(ROOT, "benchmarks", "dataset", "review1_dataset.json"), encoding="utf-8") as fh:
        return [(p["id"], compile_source(p["source"])) for p in json.load(fh)["programs"]]


class StrategyTests(unittest.TestCase):
    def test_all_strategies_correct_on_review1(self):
        for pid, program in review1_programs():
            n_in = sum(i.op == "input" for i in program)
            cases = generate_test_inputs(n_in, 40, seed=12345)
            for name, fn in STRATEGIES.items():
                with self.subTest(program=pid, strategy=name):
                    res = fn(program)
                    self.assertLessEqual(res.final_cost, 1.0 + 1e-12)
                    self.assertEqual(res.candidates_rejected, 0)
                    for c in cases:
                        self.assertEqual(run(program, c).observable, run(res.optimized, c).observable)

    def test_unoptimized_is_identity(self):
        program = compile_source("input a; print a * 2 + 0;")
        res = STRATEGIES["unoptimized"](program)
        self.assertEqual(res.optimized, program)
        self.assertEqual(res.final_cost, 1.0)

    def test_fixed_order_uses_only_shared_operators(self):
        ids = {op for op, _ in OPERATORS}
        self.assertTrue(set(FIXED_ORDER) <= ids)

    def test_unsafe_operator_rejected_by_all_search_strategies(self):
        program = compile_source("input a, b; x = a - b; y = x * 3; print y; print x;")
        cases = generate_test_inputs(2, 40, seed=7)
        for name in ("greedy", "beam"):
            res = STRATEGIES[name](program, operators=OPERATORS + UNSAFE_OPERATORS)
            self.assertGreater(res.candidates_rejected, 0, name)
            for c in cases:
                self.assertEqual(run(program, c).observable, run(res.optimized, c).observable)

    def test_evaluator_caches(self):
        program = compile_source("input a; print a * 2;")
        ev = Evaluator(program)
        ev.evaluate(program)
        ev.evaluate(program)
        self.assertEqual(ev.executed, 1)

    def test_weight_configs_normalised(self):
        program = compile_source("input a, b; print (a + b) * (a + b);")
        ev = Evaluator(program)
        for name, w in WEIGHT_CONFIGS.items():
            self.assertAlmostEqual(sum(w.values()), 1.0, msg=name)
            self.assertAlmostEqual(cost(ev.base, ev.base, w), 1.0, msg=name)

    def test_beam_not_worse_than_greedy_on_review1_average(self):
        progs = review1_programs()
        greedy = sum(STRATEGIES["greedy"](p).final_cost for _, p in progs)
        beam = sum(STRATEGIES["beam"](p).final_cost for _, p in progs)
        self.assertLessEqual(beam, greedy + 1e-9)

    def test_control_flow_program_all_strategies(self):
        src = "input a, b; k = 1; if (k) { x = a * 2; } else { x = b; } if (a < b) { print x; } print x + 0;"
        program = compile_source(src)
        for name, fn in STRATEGIES.items():
            res = fn(program)
            for c in generate_test_inputs(2, 40, seed=3):
                self.assertEqual(run(program, c).observable, run(res.optimized, c).observable, name)


class BackendAndTimingTests(unittest.TestCase):
    def test_compiled_backend_rejects_control_flow(self):
        with self.assertRaises(ValueError):
            compile_tac(compile_source("input a; if (a) { print a; }"))

    def test_timing_returns_raw_samples(self):
        p = compile_source("input a; print a * 2;")
        for backend in ("interp", "compiled"):
            samples = time_variants({"x": p, "y": p}, [(1,), (2,)], backend=backend, warmup=1, repeats=4)
            self.assertEqual({k: len(v) for k, v in samples.items()}, {"x": 4, "y": 4})
            s = summarize(samples["x"], per=2)
            self.assertLessEqual(s["q1_ns"], s["median_ns"])
            self.assertLessEqual(s["median_ns"], s["q3_ns"])

    def test_measure_excludes_labels(self):
        p = compile_source("input a; if (a) { print a; }")
        m = measure(p, [run(p, (1,))])
        self.assertEqual(m.instructions, sum(i.op != "label" for i in p))


if __name__ == "__main__":
    unittest.main()
