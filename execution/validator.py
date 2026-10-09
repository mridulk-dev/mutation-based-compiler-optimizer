"""Differential-testing correctness validation.

A candidate is accepted only if, on every test input vector, it produces the
same observable behaviour (printed outputs and error status) as the original
program. This is testing, not proof: it can only reject, never certify.
"""

import random

from .interpreter import run

SPECIAL_VALUES = (0, 1, -1, 2, -2, 7, 1000, -999)


def count_inputs(program):
    return sum(1 for ins in program if ins.op == "input")


def generate_test_inputs(n_inputs, n_cases=25, seed=42):
    """Deterministic test vectors: a few edge-case vectors, the rest random."""
    rng = random.Random(seed)
    cases = []
    for special in SPECIAL_VALUES:
        if len(cases) >= n_cases:
            break
        cases.append(tuple([special] * n_inputs))
    while len(cases) < n_cases:
        cases.append(tuple(rng.randint(-100, 100) for _ in range(n_inputs)))
    return cases


def run_suite(program, cases):
    return [run(program, case) for case in cases]


def equivalent(reference_results, candidate_results):
    return all(r.observable == c.observable for r, c in zip(reference_results, candidate_results))


class Validator:
    def __init__(self, original, n_cases=25, seed=42):
        self.cases = generate_test_inputs(count_inputs(original), n_cases, seed)
        self.reference = run_suite(original, self.cases)

    def check(self, candidate):
        """Returns (is_valid, results) so callers can reuse the runs for costing."""
        results = run_suite(candidate, self.cases)
        return equivalent(self.reference, results), results
