"""Replay every saved regression case (tests/regressions/cases.json) and any
failure the fuzzer saved under tests/regressions/found/."""

import glob
import itertools
import json
import os
import unittest

from execution.interpreter import run
from execution.reference import run_source_detailed
from execution.validator import Validator
from frontend.tac import compile_source, format_tac, parse_tac
from optimizer.strategies import STRATEGIES

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "regressions", "cases.json"), encoding="utf-8") as fh:
    CASES = {c["id"]: c for c in json.load(fh)["cases"]}


def exhaustive_ok(source, optimized, lo=-4, hi=4):
    n = sum(1 for i in compile_source(source) if i.op == "input")
    for v in itertools.product(range(lo, hi + 1), repeat=n):
        if run(optimized, v).observable != run_source_detailed(source, v).observable:
            return v
    return None


class RegressionTests(unittest.TestCase):
    def test_optimisation_regressions(self):
        for cid in ("REG-001", "REG-002", "REG-004"):
            src = CASES[cid]["source"]
            for name in ("fixed", "greedy", "beam"):
                with self.subTest(case=cid, strategy=name):
                    res = STRATEGIES[name](compile_source(src))
                    self.assertIsNone(exhaustive_ok(src, res.optimized))
                    self.assertEqual(res.candidates_rejected, 0)

    def test_reg003_missing_label(self):
        r = run(parse_tac(CASES["REG-003"]["tac"]), [])
        self.assertIn(CASES["REG-003"]["expect_error_contains"], r.error)

    def test_reg004_roundtrip(self):
        program = compile_source(CASES["REG-004"]["source"])
        self.assertEqual(parse_tac(format_tac(program, numbered=False)), program)

    def test_reg005_validator_v2_rejects_known_miss(self):
        c = CASES["REG-005"]
        original, mutant = compile_source(c["source"]), parse_tac(c["mutant_tac"])
        self.assertTrue(Validator(original).check(mutant)[0], "documents the Review-1 protocol miss")
        self.assertFalse(Validator(original, structured=True).check(mutant)[0])

    def test_saved_fuzz_failures_now_pass(self):
        for path in sorted(glob.glob(os.path.join(HERE, "regressions", "found", "*.src"))):
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            with self.subTest(case=os.path.basename(path)):
                res = STRATEGIES["greedy"](compile_source(src))
                self.assertIsNone(exhaustive_ok(src, res.optimized, -3, 3))


if __name__ == "__main__":
    unittest.main()
