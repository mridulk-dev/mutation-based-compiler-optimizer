"""Validation protocols: Review-1 and v2 stay byte-stable; v3 closes the coverage
defects found by the validator self-evaluation (docs/validator_investigation.md)."""

import hashlib
import json
import os
import unittest

from execution.interpreter import run
from execution.validator import PROTOCOLS, Validator, generate_test_inputs
from frontend.tac import compile_source, parse_tac

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "regressions", "cases.json"), encoding="utf-8") as fh:
    CASES = [c for c in json.load(fh)["cases"] if c["kind"] == "validator-false-acceptance"]


def digest(vectors):
    return hashlib.sha256(repr(vectors).encode()).hexdigest()[:16]


class ProtocolDefinitionTests(unittest.TestCase):
    def test_review1_and_v2_vectors_unchanged(self):
        # pinned digests of the vector lists used for the Review-1 and v2 results
        self.assertEqual({n: digest(generate_test_inputs(n)) for n in range(1, 5)}, REVIEW1_DIGESTS)
        self.assertEqual({n: digest(generate_test_inputs(n, structured=True)) for n in range(1, 5)}, V2_DIGESTS)
        self.assertEqual(generate_test_inputs(3, structured=True), generate_test_inputs(3, protocol="v2"))

    def test_default_is_review1(self):
        p = compile_source("input a, b; print a - b;")
        self.assertEqual(Validator(p).protocol, "review1")
        self.assertEqual(len(Validator(p).cases), 25)
        with self.assertRaises(ValueError):
            generate_test_inputs(2, protocol="v9")

    def test_v3_is_superset_of_v2(self):
        for n in range(0, 6):
            v2, v3 = generate_test_inputs(n, protocol="v2"), generate_test_inputs(n, protocol="v3")
            self.assertEqual(v3[:len(v2)], v2)
            self.assertLessEqual(set(v2), set(v3))

    def test_v3_gives_inputs_distinct_values(self):
        for n in (2, 3, 4):
            vs = generate_test_inputs(n, protocol="v3")
            self.assertGreaterEqual(sum(len(set(v)) == n for v in vs), 20, n)
            r1 = generate_test_inputs(n)
            self.assertTrue(all(len(set(v)) == 1 for v in r1[:8]), "Review-1 specials are all-equal")

    def test_v3_covers_large_and_small_ranges(self):
        for n in (1, 2, 3, 4):
            vs = generate_test_inputs(n, protocol="v3")
            self.assertTrue(any(max(abs(x) for x in v) > 10 ** 4 for v in vs), n)
        self.assertTrue({(x,) for x in range(-16, 17)} <= set(generate_test_inputs(1, protocol="v3")))
        self.assertTrue({(a, b) for a in range(-3, 4) for b in range(-3, 4)} <= set(generate_test_inputs(2, protocol="v3")))

    def test_v3_deterministic(self):
        self.assertEqual(generate_test_inputs(3, protocol="v3"), generate_test_inputs(3, protocol="v3"))
        self.assertEqual(set(PROTOCOLS), {"review1", "v2", "v3"})


class DiscoveredFalseAcceptanceTests(unittest.TestCase):
    """Every saved false acceptance really differs from its original; the ones v3 fixes
    are rejected by v3 (and were accepted by the older protocols)."""

    def test_saved_cases_are_genuine_differences(self):
        self.assertGreaterEqual(len(CASES), 7)
        for c in CASES:
            original, mutant = compile_source(c["source"]), parse_tac(c["mutant_tac"])
            with self.subTest(case=c["id"]):
                self.assertNotEqual(run(original, c["witness_input"]).observable,
                                    run(mutant, c["witness_input"]).observable)

    def test_v3_rejects_fixed_failure_modes(self):
        fixed = [c for c in CASES if c["status"] == "fixed-by-v3"]
        self.assertGreaterEqual(len(fixed), 4)
        for c in fixed:
            original, mutant = compile_source(c["source"]), parse_tac(c["mutant_tac"])
            with self.subTest(case=c["id"]):
                self.assertTrue(Validator(original).check(mutant)[0], "documents the Review-1 miss")
                self.assertTrue(Validator(original, protocol="v2").check(mutant)[0], "documents the v2 miss")
                self.assertFalse(Validator(original, protocol="v3").check(mutant)[0])

    def test_known_remaining_false_acceptances(self):
        # Known limitation of finite testing (thin zero sets / narrow value regions).
        # If a future protocol catches one, update docs/validator_investigation.md.
        remaining = [c for c in CASES if c["status"] == "remains-accepted-by-v3"]
        self.assertGreaterEqual(len(remaining), 3)
        for c in remaining:
            original, mutant = compile_source(c["source"]), parse_tac(c["mutant_tac"])
            with self.subTest(case=c["id"]):
                self.assertTrue(Validator(original, protocol="v3").check(mutant)[0])

    def test_single_point_blind_spot_documented(self):
        original = compile_source("input a; print a + 1;")
        wrong = compile_source("input a; print a + 1 + 1 / (a - 4242) * 0 + (a - 4242) / 4242 * 0;")
        self.assertNotEqual(run(original, [4242]).observable, run(wrong, [4242]).observable)
        for p in PROTOCOLS:
            self.assertTrue(Validator(original, protocol=p).check(wrong)[0], p)


# digests of the vectors produced by the previously committed generator (commit cd8430b)
REVIEW1_DIGESTS = {1: 'd8bb6b72540f0402', 2: 'b6560ff9838b7847', 3: '067c95f7fec81cbc', 4: '6516c3913c8c07d6'}
V2_DIGESTS = {1: 'd8bb6b72540f0402', 2: '6f2768fd6102ea79', 3: '9395532b6cedcb13', 4: '948b7cdae8dab2da'}

if __name__ == "__main__":
    unittest.main()
