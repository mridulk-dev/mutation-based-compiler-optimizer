"""Differential-testing correctness validation.

A candidate is accepted only if, on every test input vector, it produces the
same observable behaviour (printed outputs and error status) as the original
program. This is testing, not proof: it can only reject, never certify.
"""

import random

from .interpreter import run

SPECIAL_VALUES = (0, 1, -1, 2, -2, 7, 1000, -999)
FILL = (3, -5, 7, -2, 11, -13)


def structured_vectors(n_inputs):
    """Vectors that make differences/divisors zero: one input 0 with the others
    non-zero and of mixed sign, and every pair of inputs equal. Added after the
    validator self-evaluation found misses on exactly these input shapes."""
    out = []
    for k in range(n_inputs):
        v = [FILL[j % len(FILL)] for j in range(n_inputs)]
        v[k] = 0
        out.append(tuple(v))
    for i in range(n_inputs):
        for j in range(i + 1, n_inputs):
            v = [FILL[q % len(FILL)] for q in range(n_inputs)]
            v[j] = v[i]
            out.append(tuple(v))
            v = list(v)
            v[i] = v[j] = -v[i]
            out.append(tuple(v))
    return out


def coverage_vectors(n_inputs, seed=42):
    """Protocol v3 additions (found by the validator self-evaluation, see
    docs/validator_investigation.md):

    * rotated specials - the Review-1 special values are applied to *all* inputs
      at once, so a fault that swaps one input for another can never show on
      them; rotating the list gives every input a different special value;
    * large random vectors in [-10^5, 10^5] - the Review-1 random range
      [-100, 100] hides faults behind `x / 100`-style thresholds;
    * small-range coverage - divisors such as `i1*i1 - i0` are zero only on a
      thin set of small inputs: exhaustive over [-16, 16] for one input,
      exhaustive over [-3, 3]^2 for two inputs, 40 seeded samples from
      [-3, 3]^n otherwise.
    """
    out = []
    k = len(SPECIAL_VALUES)
    for r in range(1, k):
        out.append(tuple(SPECIAL_VALUES[(r + j) % k] for j in range(n_inputs)))
    rng = random.Random(f"v3:{seed}:{n_inputs}")
    out += [tuple(rng.randint(-10 ** 5, 10 ** 5) for _ in range(n_inputs)) for _ in range(8)]
    if n_inputs == 1:
        out += [(v,) for v in range(-16, 17)]
    elif n_inputs == 2:
        out += [(a, b) for a in range(-3, 4) for b in range(-3, 4)]
    elif n_inputs > 2:
        out += [tuple(rng.randint(-3, 3) for _ in range(n_inputs)) for _ in range(40)]
    return out


PROTOCOLS = ("review1", "v2", "v3")


def count_inputs(program):
    return sum(1 for ins in program if ins.op == "input")


def generate_test_inputs(n_inputs, n_cases=25, seed=42, structured=False, protocol=None):
    """Deterministic test vectors.

    review1 (default): 8 all-equal special vectors + seeded random in [-100, 100], 25 in total
    v2 (structured=True): review1 + structured_vectors()
    v3: v2 + coverage_vectors()
    The default is unchanged so Review-1 results stay comparable."""
    protocol = protocol or ("v2" if structured else "review1")
    if protocol not in PROTOCOLS:
        raise ValueError(f"unknown validation protocol {protocol!r}")
    rng = random.Random(seed)
    cases = []
    for special in SPECIAL_VALUES:
        if len(cases) >= n_cases:
            break
        cases.append(tuple([special] * n_inputs))
    while len(cases) < n_cases:
        cases.append(tuple(rng.randint(-100, 100) for _ in range(n_inputs)))
    extra = []
    if protocol in ("v2", "v3"):
        extra += structured_vectors(n_inputs)
    if protocol == "v3":
        extra += coverage_vectors(n_inputs, seed)
    seen = set(cases)
    for v in extra:
        if v not in seen:
            seen.add(v)
            cases.append(v)
    return cases


def run_suite(program, cases):
    return [run(program, case) for case in cases]


def equivalent(reference_results, candidate_results):
    return all(r.observable == c.observable for r, c in zip(reference_results, candidate_results))


class Validator:
    def __init__(self, original, n_cases=25, seed=42, structured=False, protocol=None):
        self.protocol = protocol or ("v2" if structured else "review1")
        self.cases = generate_test_inputs(count_inputs(original), n_cases, seed, protocol=self.protocol)
        self.reference = run_suite(original, self.cases)

    def check(self, candidate):
        """Returns (is_valid, results) so callers can reuse the runs for costing."""
        results = run_suite(candidate, self.cases)
        return equivalent(self.reference, results), results
