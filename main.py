"""Automated mutation-based TAC optimiser - command-line driver.

Examples:
    python main.py                               # all benchmarks, results + plots
    python main.py benchmarks/b3_cse.src         # one program, full trace
    python main.py --unsafe                      # also try an unsafe operator to show rejection
"""

import argparse
import csv
import glob
import json
import os
import sys

from frontend.lexer import LexError
from frontend.parser import ParseError
from frontend.tac import compile_source, format_tac
from optimizer.cost import WEIGHTS
from optimizer.mutations import OPERATOR_NAMES, OPERATORS, UNSAFE_OPERATORS
from optimizer.search import optimize

ROOT = os.path.dirname(os.path.abspath(__file__))


def collect_sources(paths):
    files = []
    for path in paths:
        if os.path.isdir(path):
            files.extend(sorted(glob.glob(os.path.join(path, "*.src"))))
        else:
            files.append(path)
    return files


def side_by_side(left, right, titles=("ORIGINAL TAC", "OPTIMISED TAC")):
    left_lines = format_tac(left).splitlines()
    right_lines = format_tac(right).splitlines()
    width = max([len(titles[0])] + [len(s) for s in left_lines]) + 4
    rows = [f"{titles[0]:<{width}}{titles[1]}", f"{'-' * (width - 4):<{width}}{'-' * len(titles[1])}"]
    for i in range(max(len(left_lines), len(right_lines))):
        a = left_lines[i] if i < len(left_lines) else ""
        b = right_lines[i] if i < len(right_lines) else ""
        rows.append(f"{a:<{width}}{b}")
    return "\n".join(rows)


def metrics_table(res):
    b, f = res.base_metrics, res.final_metrics
    rows = ["  metric          weight   original  optimised   change"]
    for key in WEIGHTS:
        before, after = getattr(b, key), getattr(f, key)
        change = (after - before) / before * 100 if before else 0.0
        rows.append(f"  {key:<14}  {WEIGHTS[key]:>5.1f}   {before:>8.1f}  {after:>9.1f}  {change:>+6.1f}%")
    rows.append(f"  {'COST':<14}  {'':>5}   {1.0:>8.3f}  {res.final_cost:>9.3f}  {(res.final_cost - 1) * 100:>+6.1f}%")
    return "\n".join(rows)


def save_result(name, res, out_dir):
    bench_dir = os.path.join(out_dir, name)
    os.makedirs(bench_dir, exist_ok=True)
    with open(os.path.join(bench_dir, "original.tac"), "w") as fh:
        fh.write(format_tac(res.original, numbered=False) + "\n")
    with open(os.path.join(bench_dir, "optimized.tac"), "w") as fh:
        fh.write(format_tac(res.optimized, numbered=False) + "\n")
    trace = {
        "benchmark": name,
        "base_metrics": res.base_metrics.as_dict(),
        "final_metrics": res.final_metrics.as_dict(),
        "final_cost": res.final_cost,
        "candidates_evaluated": res.candidates_total,
        "candidates_rejected": res.rejected_total,
        "rejected_by_operator": dict(res.rejected_by_op),
        "applied_by_operator": dict(res.applied_by_op),
        "seconds": res.seconds,
        "steps": [vars(step) for step in res.steps],
    }
    with open(os.path.join(bench_dir, "trace.json"), "w") as fh:
        json.dump(trace, fh, indent=2)


def write_summary(results, out_dir):
    path = os.path.join(out_dir, "summary.csv")
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["benchmark", "instr_before", "instr_after", "arith_before", "arith_after",
                         "temps_before", "temps_after", "cycles_before", "cycles_after", "final_cost",
                         "iterations", "candidates", "rejected", "seconds"])
        for name, res in results.items():
            b, f = res.base_metrics, res.final_metrics
            writer.writerow([name, b.instructions, f.instructions, b.arith_ops, f.arith_ops, b.temps, f.temps,
                             b.cycles, f.cycles, round(res.final_cost, 4), len(res.steps),
                             res.candidates_total, res.rejected_total, round(res.seconds, 3)])
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Mutation-based TAC optimiser")
    parser.add_argument("paths", nargs="*", default=[os.path.join(ROOT, "benchmarks")],
                        help="source files or directories of .src files (default: benchmarks/)")
    parser.add_argument("--cases", type=int, default=25, help="test input vectors for validation")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-iters", type=int, default=500)
    parser.add_argument("--unsafe", action="store_true",
                        help="add an unsafe operator to demonstrate validator rejections")
    parser.add_argument("--quiet", action="store_true", help="hide per-iteration trace")
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--out", default=os.path.join(ROOT, "results", "local"),
                        help="output directory (default results/local/, git-ignored; "
                             "use --out results to regenerate the committed Review-1 artifacts)")
    args = parser.parse_args(argv)

    operators = OPERATORS + (UNSAFE_OPERATORS if args.unsafe else [])
    files = collect_sources(args.paths)
    if not files:
        print("no .src files found", file=sys.stderr)
        return 1

    results = {}
    for path in files:
        name = os.path.splitext(os.path.basename(path))[0]
        print("=" * 78)
        print(f"BENCHMARK {name}  ({path})")
        print("=" * 78)
        with open(path) as fh:
            source = fh.read()
        try:
            program = compile_source(source)
        except (LexError, ParseError) as exc:
            print(f"  compile error: {exc}")
            continue

        print("\nSearch trace:")
        res = optimize(program, operators, max_iters=args.max_iters, n_cases=args.cases, seed=args.seed,
                       log=None if args.quiet else print)
        print(f"  {len(res.steps)} mutations accepted, {res.candidates_total} candidates evaluated, "
              f"{res.rejected_total} rejected by validation, {res.seconds:.2f}s")
        if res.rejected_by_op:
            print("  rejections by operator: "
                  + ", ".join(f"{op}={n}" for op, n in sorted(res.rejected_by_op.items())))
        print()
        print(side_by_side(res.original, res.optimized))
        print()
        print(metrics_table(res))
        print()
        save_result(name, res, args.out)
        results[name] = res

    if not results:
        return 1

    os.makedirs(args.out, exist_ok=True)
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"  {'benchmark':<18}{'instr':>10}{'arith':>10}{'temps':>10}{'cycles':>14}{'cost':>8}{'iters':>7}")
    for name, res in results.items():
        b, f = res.base_metrics, res.final_metrics
        print(f"  {name:<18}{f'{b.instructions}->{f.instructions}':>10}{f'{b.arith_ops}->{f.arith_ops}':>10}"
              f"{f'{b.temps}->{f.temps}':>10}{f'{b.cycles:.0f}->{f.cycles:.0f}':>14}"
              f"{res.final_cost:>8.3f}{len(res.steps):>7}")
    applied = {}
    for res in results.values():
        for op, n in res.applied_by_op.items():
            applied[op] = applied.get(op, 0) + n
    print("\n  operators applied: " + ", ".join(f"{op} {OPERATOR_NAMES[op]}={n}" for op, n in sorted(applied.items())))
    print(f"\n  results written to {write_summary(results, args.out)} and {args.out}/<benchmark>/")

    if not args.no_plots:
        try:
            from visualization.plots import make_all
        except ImportError:
            print("  matplotlib not installed - skipping plots")
        else:
            for path in make_all(results, os.path.join(args.out, "plots")).values():
                print(f"  plot: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
