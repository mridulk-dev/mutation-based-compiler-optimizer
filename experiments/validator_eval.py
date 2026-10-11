"""Evaluate the differential-testing validator with deliberately wrong mutants.

    python -m experiments.validator_eval                 # all accepted dataset programs
    python -m experiments.validator_eval --limit 50

For every program, mutants are produced by fault-injection operators that are
wrong in general (but may happen to be equivalent on a given program):

    op-swap        change an arithmetic operator (+ <-> -, * -> +, / -> *, % -> /)
    operand-swap   swap the operands of - / %
    const-change   add 1 to a constant operand
    drop-print     delete a print
    swap-prints    exchange two adjacent prints
    drop-trap      delete a possibly-trapping division (hides division-by-zero)
    wrong-var      replace an operand variable by another defined variable
    unsafe-reorder the MX operator (swap dependent adjacent instructions)

Each mutant is (1) judged by the validator exactly as during search (25 seeded
vectors) and (2) compared with the original on 300 held-out inputs (ground
truth proxy). Outcomes:
    rejected                         validator says "different"
    accepted_equivalent              validator says "same", no held-out input differs
    accepted_but_different (MISS)    validator says "same", a held-out input differs
A MISS is a validator false negative; finite testing cannot rule these out.
Writes results/final/validator_eval.json.
"""

import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict
from dataclasses import replace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from execution.interpreter import run  # noqa: E402
from execution.validator import Validator  # noqa: E402
from frontend.tac import compile_source, is_const, is_var  # noqa: E402
from optimizer.mutations import may_trap, unchecked_reordering  # noqa: E402

SWAP = {"+": "-", "-": "+", "*": "+", "/": "*", "%": "/"}


def mutants(program):
    defined = []
    for i, ins in enumerate(program):
        if ins.op in SWAP:
            yield "op-swap", program[:i] + (replace(ins, op=SWAP[ins.op]),) + program[i + 1:]
        if ins.op in ("-", "/", "%") and ins.a != ins.b:
            yield "operand-swap", program[:i] + (replace(ins, a=ins.b, b=ins.a),) + program[i + 1:]
        for f in ("a", "b"):
            v = getattr(ins, f)
            if is_const(v) and ins.op not in ("label", "goto", "ifz"):
                yield "const-change", program[:i] + (replace(ins, **{f: v + 1}),) + program[i + 1:]
            if is_var(v) and len(defined) > 1:
                other = next((d for d in reversed(defined) if d != v), None)
                if other:
                    yield "wrong-var", program[:i] + (replace(ins, **{f: other}),) + program[i + 1:]
        if ins.op == "print":
            yield "drop-print", program[:i] + program[i + 1:]
            if i + 1 < len(program) and program[i + 1].op == "print" and program[i + 1] != ins:
                yield "swap-prints", program[:i] + (program[i + 1], ins) + program[i + 2:]
        if may_trap(ins):
            yield "drop-trap", program[:i] + program[i + 1:]
        if ins.dest is not None and ins.dest not in defined:
            defined.append(ins.dest)
    for m in unchecked_reordering(program):
        yield "unsafe-reorder", m.program


def heldout(n_inputs, n=300, seed=99):
    rng = random.Random(f"validator-eval:{seed}")
    cases = [tuple(rng.choice([0, 1, -1, 2, -2, 3]) for _ in range(n_inputs)) for _ in range(60)]
    cases += [tuple(rng.randint(-10 ** 5, 10 ** 5) for _ in range(n_inputs)) for _ in range(n - 60)]
    return cases


def evaluate(records, per_program_cap=60, structured=False):
    outcome = defaultdict(Counter)
    misses = []
    for rec in records:
        program = compile_source(rec["source"])
        validator = Validator(program, structured=structured)
        cases = heldout(rec["n_inputs"])
        ref = [run(program, c).observable for c in cases]
        seen = set()
        n = 0
        for kind, mutant in mutants(program):
            if mutant in seen or mutant == program:
                continue
            seen.add(mutant)
            n += 1
            if n > per_program_cap:
                break
            valid, _ = validator.check(mutant)
            if not valid:
                outcome[kind]["rejected"] += 1
                continue
            diff = next((c for c, r in zip(cases, ref) if run(mutant, c).observable != r), None)
            if diff is None:
                outcome[kind]["accepted_equivalent"] += 1
            else:
                outcome[kind]["accepted_but_different"] += 1
                misses.append({"program_id": rec["id"], "kind": kind, "witness_input": list(diff)})
    total = Counter()
    for c in outcome.values():
        total.update(c)
    decided_wrong = total["rejected"] + total["accepted_but_different"]
    return {
        "protocol": "v2 (Review-1 vectors + structured vectors)" if structured else "review1 (25 vectors)",
        "validation_vectors_example_3_inputs": len(Validator(compile_source("input a, b, c; print a;"),
                                                               structured=structured).cases),
        "programs": len(records),
        "mutants": sum(total.values()),
        "totals": dict(total),
        "by_kind": {k: dict(v) for k, v in sorted(outcome.items())},
        "detection_rate_on_nonequivalent": round(total["rejected"] / decided_wrong, 6) if decided_wrong else None,
        "misses": misses[:200],
        "note": "accepted_equivalent mutants are equivalent on 300 held-out inputs (not proven equivalent); "
                "misses are validator false negatives that finite testing cannot exclude",
    }


def blind_spot_demo():
    """A program/mutant pair that differs only at one input value, far from the
    validator's vectors: documents that differential testing is not a proof."""
    original = compile_source("input a; print a + 1;")
    wrong = compile_source("input a; print a + 1 + 1 / (a - 4242) * 0 + (a - 4242) / 4242 * 0;")
    # wrong traps (division by zero) only when a == 4242
    valid, _ = Validator(original).check(wrong)
    return {"original": "input a; print a + 1;",
            "mutant_traps_only_at": 4242,
            "validator_accepts_mutant": valid,
            "differs_at_4242": run(original, [4242]).observable != run(wrong, [4242]).observable}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=os.path.join(ROOT, "dataset", "miniarith-v1", "manifest.jsonl"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "final", "validator_eval.json"))
    args = ap.parse_args(argv)
    with open(args.manifest, encoding="utf-8") as fh:
        records = [r for r in map(json.loads, fh) if r["status"] == "accepted"]
    if args.limit:
        records = records[:: max(1, len(records) // args.limit)][: args.limit]
    result = {"review1": evaluate(records), "v2": evaluate(records, structured=True),
              "blind_spot_demo": blind_spot_demo()}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(result, fh, indent=2)
        fh.write("\n")
    for name in ("review1", "v2"):
        r = result[name]
        t = r["totals"]
        print(f"[{r['protocol']}] {r['mutants']} mutants on {r['programs']} programs: rejected {t.get('rejected', 0)}, "
              f"accepted-equivalent {t.get('accepted_equivalent', 0)}, MISSED {t.get('accepted_but_different', 0)}; "
              f"detection rate {r['detection_rate_on_nonequivalent']}")
    print(f"blind-spot demo: validator accepts a mutant that differs only at a=4242: "
          f"{result['blind_spot_demo']['validator_accepts_mutant']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
