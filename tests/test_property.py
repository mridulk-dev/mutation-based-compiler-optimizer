"""Seeded property-based tests (stdlib `random`, fixed seeds -> reproducible).

hypothesis is not a project dependency, so properties are checked over
deterministic pseudo-random program/input samples. Each test names its seed;
a failure message includes the generated program so it can be replayed.
"""

import random
import re
import unittest

from benchmarks.randomgen import generate_program
from execution.interpreter import run
from execution.pycodegen import compile_tac
from execution.reference import run_source_detailed
from frontend.tac import canonical_tac, compile_source, format_tac, parse_tac
from optimizer.mutations import generate_mutations
from optimizer.strategies import STRATEGIES
from tests.fuzz import random_cf_program


def vectors(n_inputs, seed, n=60):
    rng = random.Random(seed)
    out = [tuple([v] * n_inputs) for v in (0, 1, -1, 2, -3)]
    out += [tuple(rng.randint(-1000, 1000) for _ in range(n_inputs)) for _ in range(n - len(out))]
    return out


def sample_programs(seed, n, cf=False):
    rng = random.Random(seed)
    if cf:
        return [random_cf_program(rng) for _ in range(n)]
    return [generate_program(f"prop{seed}", f"P{k}") for k in range(n)]


class PropertyTests(unittest.TestCase):
    def test_tac_and_compiled_match_reference(self):
        for src in sample_programs(101, 40):
            program = compile_source(src)
            fn = compile_tac(program)
            for v in vectors(sum(i.op == "input" for i in program), 101, 30):
                ref = run_source_detailed(src, v).observable
                self.assertEqual(run(program, v).observable, ref, src)
                self.assertEqual(fn(v), ref, src)

    def test_cf_tac_matches_reference(self):
        for src in sample_programs(202, 40, cf=True):
            program = compile_source(src)
            for v in vectors(sum(i.op == "input" for i in program), 202, 30):
                self.assertEqual(run(program, v).observable, run_source_detailed(src, v).observable, src)

    def test_every_single_mutation_preserves_behaviour(self):
        for cf in (False, True):
            for src in sample_programs(303, 15, cf=cf):
                program = compile_source(src)
                vs = vectors(sum(i.op == "input" for i in program), 303, 40)
                expected = [run(program, v).observable for v in vs]
                for cand in generate_mutations(program):
                    got = [run(cand.program, v).observable for v in vs]
                    self.assertEqual(got, expected, f"{cand.op_id} {cand.description}\n{src}")

    def test_all_strategies_preserve_behaviour_on_unseen_inputs(self):
        for cf in (False, True):
            for src in sample_programs(404, 3, cf=cf):
                program = compile_source(src)
                vs = vectors(sum(i.op == "input" for i in program), 4040, 60)  # not the validator's inputs
                for name in ("fixed", "greedy", "beam"):
                    res = STRATEGIES[name](program, width=2) if name == "beam" else STRATEGIES[name](program)
                    for v in vs:
                        self.assertEqual(run(res.optimized, v).observable,
                                         run_source_detailed(src, v).observable, f"{name}\n{src}")

    def test_optimisation_never_increases_greedy_cost(self):
        for src in sample_programs(505, 5):
            res = STRATEGIES["greedy"](compile_source(src))
            self.assertLessEqual(res.final_cost, 1.0 + 1e-12)

    def test_tac_text_roundtrip(self):
        for cf in (False, True):
            for src in sample_programs(606, 20, cf=cf):
                program = compile_source(src)
                self.assertEqual(parse_tac(format_tac(program, numbered=False)), program)

    def test_canonical_tac_invariant_under_renaming(self):
        for src in sample_programs(707, 20):
            body = src.split("\n", 1)[1]  # drop the comment header
            renamed = re.sub(r"\b(i\d|[xyzwpqrs])\b", lambda mm: f"renamed_{mm.group(1)}_v", body)
            self.assertNotEqual(renamed, body)
            a, b = compile_source(src), compile_source(renamed)
            self.assertEqual(canonical_tac(a), canonical_tac(b))

    def test_strategies_are_deterministic(self):
        for src in sample_programs(808, 2):
            program = compile_source(src)
            for name in ("fixed", "greedy", "beam"):
                kw = {"width": 2} if name == "beam" else {}
                r1, r2 = STRATEGIES[name](program, **kw), STRATEGIES[name](program, **kw)
                self.assertEqual(r1.optimized, r2.optimized)
                self.assertEqual(r1.candidates_evaluated, r2.candidates_evaluated)


if __name__ == "__main__":
    unittest.main()
