"""Run the optimiser on every program of the Review-1 dataset.

    python -m benchmarks.generate_dataset   # (re)create the dataset
    python run_dataset.py                   # optimise all programs, write results

Writes (all numbers come from executing the programs in this run):
    results/results.csv        one row per program
    results/candidate_log.csv  one row per candidate evaluated by the search
    results/summary.json       dataset/config info, totals, per-category and per-operator stats
"""

import argparse
import csv
import hashlib
import json
import os
import platform
import sys
import time
from collections import Counter, defaultdict

from benchmarks.generate_dataset import DATASET_PATH, generate
from execution.interpreter import CYCLES, STALL_CYCLES, run
from execution.reference import ast_run
from frontend.parser import parse
from frontend.tac import compile_source
from optimizer.cost import WEIGHTS
from optimizer.mutations import OPERATOR_NAMES, OPERATORS
from optimizer.search import optimize
from verify_pipeline import fresh_inputs

ROOT = os.path.dirname(os.path.abspath(__file__))
METRICS = ("instructions", "arith_ops", "temps", "cycles")


def dataset_tests(program, tests):
    """Run a TAC program on the dataset's test inputs; count exact matches."""
    passed = 0
    for t in tests:
        r = run(program, t["inputs"])
        if list(r.outputs) == t["expected_outputs"] and (r.error is not None) == t["expected_error"]:
            passed += 1
    return passed


def pct_change(before, after):
    return round((after - before) / before * 100, 2) if before else 0.0


def run_program(prog, args):
    program = compile_source(prog["source"])
    log = []
    res = optimize(program, OPERATORS, n_cases=args.cases, seed=args.seed, on_candidate=log.append)
    accepted = {(s.iteration, s.candidate_index) for s in res.steps}

    ast = parse(prog["source"])
    fresh = fresh_inputs(len(prog["inputs"]), n=args.fresh, seed=args.seed)
    fresh_ok = sum(ast_run(ast, c) == (r.outputs, r.error is not None)
                   for c in fresh for r in [run(res.optimized, c)])

    b, f = res.base_metrics, res.final_metrics
    row = {
        "program_id": prog["id"],
        "category": prog["category"],
        "n_dataset_tests": len(prog["tests"]),
        "dataset_tests_passed_original": dataset_tests(program, prog["tests"]),
        "dataset_tests_passed_optimized": dataset_tests(res.optimized, prog["tests"]),
        "fresh_inputs_checked": len(fresh),
        "fresh_inputs_matching_reference": fresh_ok,
    }
    for m in METRICS:
        row[f"{m}_before"] = round(getattr(b, m), 4)
        row[f"{m}_after"] = round(getattr(f, m), 4)
        row[f"{m}_change_pct"] = pct_change(getattr(b, m), getattr(f, m))
    row.update({
        "final_cost": round(res.final_cost, 6),
        "cost_reduction_pct": round((1 - res.final_cost) * 100, 2),
        "iterations": len(res.steps),
        "candidates_evaluated": res.candidates_total,
        "candidates_valid": res.candidates_total - res.rejected_total,
        "candidates_rejected": res.rejected_total,
        "operators_applied": ";".join(f"{op}:{n}" for op, n in sorted(res.applied_by_op.items())),
        "runtime_seconds": round(res.seconds, 4),
    })

    cand_rows = []
    for c in log:
        mut, met = c["mutation"], c["metrics"]
        cand_rows.append({
            "program_id": prog["id"],
            "category": prog["category"],
            "iteration": c["iteration"],
            "candidate_index": c["index"],
            "operator": mut.op_id,
            "operator_name": OPERATOR_NAMES[mut.op_id],
            "description": mut.description,
            "valid": int(c["valid"]),
            **{m: (round(getattr(met, m), 4) if met else "") for m in METRICS},
            "cost": round(c["cost"], 6) if c["cost"] is not None else "",
            "improves_current": int(c["improves"]),
            "accepted": int((c["iteration"], c["index"]) in accepted),
        })
    return row, cand_rows, res


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def mean(xs):
    xs = list(xs)
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def build_summary(dataset, digest, reproducible, rows, cand_rows, args, wall):
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    proposed, valid, applied = Counter(), Counter(), Counter()
    for c in cand_rows:
        proposed[c["operator"]] += 1
        valid[c["operator"]] += c["valid"]
        applied[c["operator"]] += c["accepted"]
    total_tests = sum(r["n_dataset_tests"] for r in rows)
    return {
        "dataset": {
            "path": os.path.relpath(DATASET_PATH, ROOT).replace("\\", "/"),
            "sha256": digest,
            "seed": dataset["seed"],
            "programs": len(rows),
            "regenerated_identically": reproducible,
            "categories": dataset["categories"],
        },
        "config": {
            "validation_cases": args.cases,
            "validation_seed": args.seed,
            "fresh_reference_inputs_per_program": args.fresh,
            "cost_weights": WEIGHTS,
            "cycle_model": {**CYCLES, "raw_stall": STALL_CYCLES},
            "search": "greedy best-improvement, stop when no valid candidate lowers cost",
            "operators": [op for op, _ in OPERATORS],
        },
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "correctness": {
            "dataset_tests_total": total_tests,
            "dataset_tests_passed_original": sum(r["dataset_tests_passed_original"] for r in rows),
            "dataset_tests_passed_optimized": sum(r["dataset_tests_passed_optimized"] for r in rows),
            "fresh_inputs_checked": sum(r["fresh_inputs_checked"] for r in rows),
            "fresh_inputs_matching_reference": sum(r["fresh_inputs_matching_reference"] for r in rows),
            "candidates_rejected_by_validator": sum(r["candidates_rejected"] for r in rows),
        },
        "totals": {
            "candidates_evaluated": len(cand_rows),
            "mutations_accepted": sum(r["iterations"] for r in rows),
            "mean_final_cost": mean(r["final_cost"] for r in rows),
            "mean_cost_reduction_pct": mean(r["cost_reduction_pct"] for r in rows),
            **{f"mean_{m}_change_pct": mean(r[f"{m}_change_pct"] for r in rows) for m in METRICS},
            "optimizer_seconds": round(sum(r["runtime_seconds"] for r in rows), 3),
            "wall_seconds": round(wall, 3),
        },
        "by_category": {
            cat: {
                "programs": [r["program_id"] for r in rs],
                "mean_final_cost": mean(r["final_cost"] for r in rs),
                "mean_cost_reduction_pct": mean(r["cost_reduction_pct"] for r in rs),
                **{f"mean_{m}_change_pct": mean(r[f"{m}_change_pct"] for r in rs) for m in METRICS},
                "mutations_accepted": sum(r["iterations"] for r in rs),
            }
            for cat, rs in by_cat.items()
        },
        "by_operator": {
            op: {"name": OPERATOR_NAMES[op], "proposed": proposed[op], "valid": valid[op], "accepted": applied[op]}
            for op, _ in OPERATORS
        },
        "per_program": {r["program_id"]: {"category": r["category"], "final_cost": r["final_cost"],
                                          "iterations": r["iterations"]} for r in rows},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Optimise every program in the Review-1 dataset")
    parser.add_argument("--dataset", default=DATASET_PATH)
    parser.add_argument("--out", default=os.path.join(ROOT, "results"))
    parser.add_argument("--cases", type=int, default=25, help="validation inputs per candidate")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fresh", type=int, default=300, help="extra unseen inputs checked against the oracle")
    args = parser.parse_args(argv)

    if not os.path.exists(args.dataset):
        print(f"dataset not found: {args.dataset}\nrun: python -m benchmarks.generate_dataset", file=sys.stderr)
        return 1
    with open(args.dataset, "rb") as fh:
        raw = fh.read()
    digest = hashlib.sha256(raw).hexdigest()
    dataset = json.loads(raw)
    reproducible = generate(dataset["seed"]) == dataset

    start = time.perf_counter()
    rows, cand_rows = [], []
    print(f"dataset {args.dataset}\n  sha256 {digest}  seed {dataset['seed']}  "
          f"regenerates identically: {reproducible}\n")
    print(f"  {'id':<4} {'category':<21} {'tests':>7} {'instr':>7} {'arith':>6} {'temps':>6} "
          f"{'cycles':>11} {'cost':>7} {'iters':>5} {'cands':>6} {'fresh ok':>9}")
    for prog in dataset["programs"]:
        row, cands, _ = run_program(prog, args)
        rows.append(row)
        cand_rows.extend(cands)
        print(f"  {row['program_id']:<4} {row['category']:<21} "
              f"{row['dataset_tests_passed_optimized']}/{row['n_dataset_tests']:<5} "
              f"{row['instructions_before']:>3}->{row['instructions_after']:<3} "
              f"{row['arith_ops_before']:>2}->{row['arith_ops_after']:<3}"
              f"{row['temps_before']:>2}->{row['temps_after']:<3}"
              f"{row['cycles_before']:>5.1f}->{row['cycles_after']:<5.1f}"
              f"{row['final_cost']:>7.3f} {row['iterations']:>5} {row['candidates_evaluated']:>6} "
              f"{row['fresh_inputs_matching_reference']:>4}/{row['fresh_inputs_checked']}")
    wall = time.perf_counter() - start

    os.makedirs(args.out, exist_ok=True)
    write_csv(os.path.join(args.out, "results.csv"), rows)
    write_csv(os.path.join(args.out, "candidate_log.csv"), cand_rows)
    summary = build_summary(dataset, digest, reproducible, rows, cand_rows, args, wall)
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    c, t = summary["correctness"], summary["totals"]
    print(f"\n  dataset tests passed (optimised): {c['dataset_tests_passed_optimized']}/{c['dataset_tests_total']}"
          f"   fresh inputs matching reference: {c['fresh_inputs_matching_reference']}/{c['fresh_inputs_checked']}")
    print(f"  candidates evaluated: {t['candidates_evaluated']}   accepted: {t['mutations_accepted']}"
          f"   mean cost {t['mean_final_cost']:.4f} ({t['mean_cost_reduction_pct']:.2f}% reduction)")
    for cat, s in summary["by_category"].items():
        print(f"    {cat:<22} mean cost {s['mean_final_cost']:.4f}  ({', '.join(s['programs'])})")
    print(f"\n  wrote {args.out}/results.csv ({len(rows)} rows), candidate_log.csv ({len(cand_rows)} rows), summary.json")
    ok = (c["dataset_tests_passed_optimized"] == c["dataset_tests_total"]
          and c["fresh_inputs_matching_reference"] == c["fresh_inputs_checked"]
          and c["dataset_tests_passed_original"] == c["dataset_tests_total"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
