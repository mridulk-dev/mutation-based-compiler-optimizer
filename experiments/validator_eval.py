"""Evaluate the differential-testing validator with deliberately wrong mutants.

    python -m experiments.validator_eval                 # all accepted dataset programs
    python -m experiments.validator_eval --limit 50

For every accepted program, mutants are produced from its naive TAC by
fault-injection operators that are wrong in general (but may happen to be
equivalent on a given program):

    op-swap        change an arithmetic operator (+ <-> -, * -> +, / -> *, % -> /)
    operand-swap   swap the operands of - / %
    const-change   add 1 to a constant operand
    drop-print     delete a print
    swap-prints    exchange two adjacent prints
    drop-trap      delete a possibly-trapping division (hides division-by-zero)
    wrong-var      replace an operand variable by the most recently defined other variable
    unsafe-reorder the MX operator (swap dependent adjacent instructions)

Mutants are de-duplicated and capped at the first `--cap` (default 60) per
program, in generation order (so later instructions of long programs are
under-sampled - a known bias).

Every mutant is judged by each validation protocol exactly as during search
(execution/validator.py: review1, v2, v3). Mutants accepted by at least one
protocol are then compared with the original program by two ORACLES, which use
seeds and ranges independent of every protocol:

    oracle300     the 300 held-out inputs used in the first evaluation (kept for comparability)
    oracle_strong exhaustive [-4, 4]^n for n <= 3 (729 vectors at n=3; a seeded sample of
                  729 for n > 3) + 400 inputs in [-1000, 1000] + 400 in [-10^6, 10^6]

Outcome per (protocol, oracle):
    rejected                         protocol says "different"
    accepted_equivalent              protocol says "same", the oracle finds no difference
    accepted_but_different (MISS)    protocol says "same", an oracle input differs
Oracles are finite too: "equivalent" means "no difference found", not proven.
Original and mutant are compared with the compiled-Python backend (equivalence
with the TAC interpreter is covered by tests and the dataset build).
Writes results/final/validator_eval.json.
"""

import argparse
import itertools
import json
import os
import random
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from execution.interpreter import run  # noqa: E402
from execution.pycodegen import compile_tac  # noqa: E402
from execution.validator import PROTOCOLS, Validator  # noqa: E402
from frontend.tac import compile_source, format_tac, is_const, is_var  # noqa: E402
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
    """The original 300-input oracle (first evaluation)."""
    rng = random.Random(f"validator-eval:{seed}")
    cases = [tuple(rng.choice([0, 1, -1, 2, -2, 3]) for _ in range(n_inputs)) for _ in range(60)]
    cases += [tuple(rng.randint(-10 ** 5, 10 ** 5) for _ in range(n_inputs)) for _ in range(n - 60)]
    return cases


def strong_oracle(n_inputs):
    rng = random.Random(f"oracle-strong:{n_inputs}")
    if n_inputs <= 3:
        cases = list(itertools.product(range(-4, 5), repeat=n_inputs))
    else:
        cases = [tuple(rng.randint(-4, 4) for _ in range(n_inputs)) for _ in range(729)]
    cases += [tuple(rng.randint(-1000, 1000) for _ in range(n_inputs)) for _ in range(400)]
    cases += [tuple(rng.randint(-10 ** 6, 10 ** 6) for _ in range(n_inputs)) for _ in range(400)]
    return cases


def first_difference(fa, fb, cases):
    for c in cases:
        if fa(c) != fb(c):
            return c
    return None


def witness_shape(w):
    tags = []
    if 0 in w:
        tags.append("zero")
    if len(set(w)) < len(w):
        tags.append("repeated")
    tags.append("small" if all(abs(t) <= 4 for t in w) else "medium" if all(abs(t) <= 1000 for t in w) else "large")
    return "+".join(tags)


def evaluate_program(task):
    rec, cap, protocols = task
    program = compile_source(rec["source"])
    validators = {p: Validator(program, protocol=p) for p in protocols}
    orig = compile_tac(program)
    o300, ostrong = heldout(rec["n_inputs"]), strong_oracle(rec["n_inputs"])
    out = []
    seen = set()
    for kind, m in mutants(program):
        if m in seen or m == program:
            continue
        seen.add(m)
        if len(seen) > cap:
            break
        verdict = {p: validators[p].check(m)[0] for p in protocols}
        entry = {"kind": kind, "accepted": verdict}
        if any(verdict.values()):
            fm = compile_tac(m)
            w300 = first_difference(orig, fm, o300)
            ws = w300 if w300 is not None else first_difference(orig, fm, ostrong)
            entry["oracle300"] = w300
            entry["oracle_strong"] = ws  # strong oracle includes the 300 set's verdict
            if ws is not None:
                o, mm = orig(ws), fm(ws)
                entry["diff"] = ("error-only-in-mutant" if mm[1] and not o[1] else
                                 "error-only-in-original" if o[1] and not mm[1] else
                                 "different-error" if o[1] and mm[1] else "value")
                entry["witness_shape"] = witness_shape(ws)
                entry["mutant_tac"] = format_tac(m, numbered=False)
                entry["changed"] = [f"{a} => {b}" for a, b in zip(program, m) if a != b][:2]
        out.append(entry)
    return rec["id"], rec["n_inputs"], out


def summarize(results, protocols):
    report = {}
    for p in protocols:
        for oracle in ("oracle300", "oracle_strong"):
            tot, by_kind, mech, misses = Counter(), defaultdict(Counter), Counter(), []
            for pid, n_in, entries in results:
                for e in entries:
                    if not e["accepted"][p]:
                        outcome = "rejected"
                    elif e.get(oracle) is None:
                        outcome = "accepted_equivalent"
                    else:
                        outcome = "accepted_but_different"
                        mech[f"{e['diff']}, witness {e['witness_shape']}"] += 1
                        misses.append({"program_id": pid, "kind": e["kind"], "n_inputs": n_in,
                                       "witness_input": list(e[oracle]), "difference": e["diff"],
                                       "changed": e["changed"]})
                    tot[outcome] += 1
                    by_kind[e["kind"]][outcome] += 1
            wrong = tot["rejected"] + tot["accepted_but_different"]
            report[f"{p}|{oracle}"] = {
                "protocol": p, "oracle": oracle, "mutants": sum(tot.values()), "totals": dict(tot),
                "detection_rate_on_nonequivalent": round(tot["rejected"] / wrong, 6) if wrong else None,
                "by_kind": {k: dict(v) for k, v in sorted(by_kind.items())},
                "miss_mechanisms": dict(mech.most_common()),
                "misses": misses[:300],
            }
    return report


def blind_spot_demo():
    """A program/mutant pair that differs only at one input value, far from every
    validation vector: documents that differential testing is not a proof."""
    original = compile_source("input a; print a + 1;")
    wrong = compile_source("input a; print a + 1 + 1 / (a - 4242) * 0 + (a - 4242) / 4242 * 0;")
    return {"original": "input a; print a + 1;",
            "mutant": "input a; print a + 1 + 1 / (a - 4242) * 0 + (a - 4242) / 4242 * 0;",
            "mutant_traps_only_at": 4242,
            "validator_accepts_mutant": {p: Validator(original, protocol=p).check(wrong)[0] for p in PROTOCOLS},
            "differs_at_4242": run(original, [4242]).observable != run(wrong, [4242]).observable}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=os.path.join(ROOT, "dataset", "miniarith-v1", "manifest.jsonl"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--cap", type=int, default=60)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "final", "validator_eval.json"))
    args = ap.parse_args(argv)
    with open(args.manifest, encoding="utf-8") as fh:
        records = [r for r in map(json.loads, fh) if r["status"] == "accepted"]
    if args.limit:
        records = records[:: max(1, len(records) // args.limit)][: args.limit]
    tasks = [(r, args.cap, PROTOCOLS) for r in records]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(evaluate_program, tasks, chunksize=4))
    report = summarize(results, PROTOCOLS)
    result = {
        "programs": len(records), "mutant_cap_per_program": args.cap, "protocols": list(PROTOCOLS),
        "vectors_per_protocol_example": {p: {n: len(Validator(compile_source(
            "input " + ", ".join(f"x{i}" for i in range(n)) + "; print 0;" if n else "print 0;"), protocol=p).cases)
            for n in (1, 2, 3, 4)} for p in PROTOCOLS},
        "evaluations": report,
        # backward-compatible keys (oracle300) used by the dashboard / report
        "review1": report["review1|oracle300"], "v2": report["v2|oracle300"], "v3": report["v3|oracle300"],
        "blind_spot_demo": blind_spot_demo(),
        "note": "accepted_equivalent = no difference found by the oracle (finite, not a proof); misses are "
                "validator false acceptances that finite testing cannot rule out",
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(result, fh, indent=2)
        fh.write("\n")
    for key, r in report.items():
        t = r["totals"]
        print(f"[{key:<22}] {r['mutants']} mutants: rejected {t.get('rejected', 0)}, accepted-equivalent "
              f"{t.get('accepted_equivalent', 0)}, MISSED {t.get('accepted_but_different', 0)}; "
              f"detection {r['detection_rate_on_nonequivalent']}")
    print(f"blind spot (traps only at a=4242) accepted: {result['blind_spot_demo']['validator_accepts_mutant']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
