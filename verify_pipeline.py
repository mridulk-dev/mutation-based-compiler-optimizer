"""Stage-by-stage verification of the whole pipeline on every benchmark.

    python verify_pipeline.py

Stages:
  0. frontend      TAC execution must match an independent AST interpreter
  1. mutations     every operator run on its own: candidate counts, and every
                   candidate from a SAFE operator must pass validation
                   (a failure here = a bug in the operator); also each operator
                   used alone in the greedy search
  2. validation    mutation-test the validator: deliberately broken programs
                   must be rejected
  3. cost          recompute the weighted cost by hand and compare
  4. search        full optimisation: strictly decreasing cost, every safe
                   candidate at every iteration valid, final program checked
                   on 300 fresh inputs against the AST oracle
Exits non-zero if any check fails.
"""

import glob
import os
import random
import sys
from dataclasses import replace

from execution.interpreter import run
from execution.reference import ast_run
from execution.validator import Validator, count_inputs, generate_test_inputs
from frontend.parser import parse
from frontend.tac import TACGenerator, is_const
from optimizer.cost import WEIGHTS, cost, measure
from optimizer.mutations import OPERATOR_NAMES, OPERATORS, generate_mutations
from optimizer.search import optimize

ROOT = os.path.dirname(os.path.abspath(__file__))
failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
        print(f"    FAIL: {message}")
    return condition


def tac_observable(program, inputs):
    r = run(program, inputs)
    return r.outputs, r.error is not None


def fresh_inputs(n_inputs, n=300, seed=2024):
    rng = random.Random(seed)
    cases = [tuple(rng.choice([0, 1, -1, 2, -3]) for _ in range(n_inputs)) for _ in range(50)]
    cases += [tuple(rng.randint(-10**6, 10**6) for _ in range(n_inputs)) for _ in range(n - 50)]
    return cases


# --- validator mutation testing ----------------------------------------------
SWAP_OP = {"+": "-", "-": "+", "*": "+", "/": "*", "%": "/", "<<": "*"}


def broken_variants(program):
    """Deliberately wrong programs: operator swap, constant perturbation,
    dropped print, swapped operands of '-' and '/'."""
    for i, ins in enumerate(program):
        if ins.op in SWAP_OP:
            yield f"[{i}] op {ins.op}->{SWAP_OP[ins.op]}", program[:i] + (replace(ins, op=SWAP_OP[ins.op]),) + program[i + 1:]
        if ins.op in ("-", "/", "%") and ins.a != ins.b:
            yield f"[{i}] swap operands", program[:i] + (replace(ins, a=ins.b, b=ins.a),) + program[i + 1:]
        for field in ("a", "b"):
            if is_const(getattr(ins, field)):
                yield f"[{i}] const+1", program[:i] + (replace(ins, **{field: getattr(ins, field) + 1}),) + program[i + 1:]
        if ins.op == "print":
            yield f"[{i}] drop print", program[:i] + program[i + 1:]


def verify(path):
    name = os.path.splitext(os.path.basename(path))[0]
    print(f"\n=== {name} " + "=" * (60 - len(name)))
    with open(path) as fh:
        source = fh.read()
    ast = parse(source)
    program = TACGenerator().gen_program(ast)
    n_in = count_inputs(program)
    fresh = fresh_inputs(n_in)

    # 0. frontend
    mismatches = sum(ast_run(ast, c) != tac_observable(program, c) for c in fresh)
    if check(mismatches == 0, f"{name}: TAC disagrees with AST oracle on {mismatches} inputs"):
        print(f"  [0] frontend   : {len(program)} TAC instrs, matches AST oracle on {len(fresh)} inputs")

    validator = Validator(program)

    # 1. mutations, individually
    print("  [1] mutations  :  op   cands  valid  solo-greedy-cost")
    for op_id, fn in OPERATORS:
        cands = fn(program)
        valid = sum(validator.check(m.program)[0] for m in cands)
        solo = optimize(program, [(op_id, fn)]).final_cost
        print(f"                    {op_id}  {len(cands):>5}  {valid:>5}  {solo:.4f}   {OPERATOR_NAMES[op_id]}")
        check(valid == len(cands), f"{name}: {op_id} produced {len(cands) - valid} invalid candidates")
        for m in cands:  # soundness beyond the 25 validation inputs
            bad = next((c for c in fresh if tac_observable(program, c) != tac_observable(m.program, c)), None)
            check(bad is None, f"{name}: {op_id} candidate '{m.description}' wrong on input {bad}")

    # 2. validation
    variants = list(broken_variants(program))
    killed, survivors = 0, []
    for desc, variant in variants:
        if not validator.check(variant)[0]:
            killed += 1
        else:
            survivors.append((desc, variant))
    # a survivor is only a validator miss if it really differs on some input
    real_misses = [d for d, v in survivors
                   if any(tac_observable(program, c) != tac_observable(v, c) for c in fresh)]
    print(f"  [2] validation : {killed}/{len(variants)} broken variants rejected, "
          f"{len(survivors) - len(real_misses)} equivalent survivors, {len(real_misses)} missed")
    check(not real_misses, f"{name}: validator accepted wrong programs: {real_misses}")
    check(validator.check(program)[0], f"{name}: validator rejects the original program")

    # 3. cost
    base = measure(program, validator.reference)
    by_hand_temps = len({i.dest for i in program if i.dest and i.dest.startswith("t") and i.dest[1:].isdigit()})
    check(base.temps == by_hand_temps, f"{name}: temp count {base.temps} != {by_hand_temps}")
    check(abs(cost(base, base) - 1.0) < 1e-12, f"{name}: original cost != 1.0")
    res = optimize(program)
    f = res.final_metrics
    manual = sum(w * getattr(f, k) / getattr(base, k) for k, w in WEIGHTS.items())
    check(abs(manual - res.final_cost) < 1e-12, f"{name}: cost mismatch {manual} vs {res.final_cost}")
    print(f"  [3] cost       : base {base.instructions}i/{base.arith_ops}a/{base.temps}t/{base.cycles:.1f}c = 1.0000 ; "
          f"final {f.instructions}i/{f.arith_ops}a/{f.temps}t/{f.cycles:.1f}c = "
          f"0.4*{f.instructions}/{base.instructions} + 0.3*{f.arith_ops}/{base.arith_ops} + "
          f"0.2*{f.temps}/{base.temps} + 0.1*{f.cycles:.1f}/{base.cycles:.1f} = {manual:.4f}")

    # 4. iterative search
    hist = res.cost_history
    check(all(b < a for a, b in zip(hist, hist[1:])), f"{name}: cost not strictly decreasing")
    check(res.rejected_total == 0, f"{name}: {res.rejected_total} safe candidates rejected during search "
                                   f"({dict(res.rejected_by_op)}) -> operator bug")
    final_after = optimize(res.optimized, n_cases=25)
    check(not final_after.steps or final_after.final_cost >= 1.0 - 1e-12,
          f"{name}: re-optimising the result still improves it (not converged)")
    wrong = sum(ast_run(ast, c) != tac_observable(res.optimized, c) for c in fresh)
    check(wrong == 0, f"{name}: optimised program wrong on {wrong}/{len(fresh)} fresh inputs")
    print(f"  [4] search     : {len(res.steps)} steps, {res.candidates_total} candidates, "
          f"{res.rejected_total} rejected, cost {res.final_cost:.4f}, "
          f"optimised == oracle on {len(fresh) - wrong}/{len(fresh)} fresh inputs")
    return res


def main():
    files = sorted(glob.glob(os.path.join(ROOT, "benchmarks", "*.src")))
    for path in files:
        verify(path)
    print("\n" + "=" * 64)
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED")
        return 1
    print(f"ALL CHECKS PASSED on {len(files)} benchmarks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
