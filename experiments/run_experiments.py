"""Run the full baseline / search comparison on a dataset manifest.

    python -m experiments.run_experiments                       # full run (all accepted programs)
    python -m experiments.run_experiments --limit 30 --out results/smoke
    python -m experiments.run_experiments --skip-timing --skip-sensitivity

Phases
  1. search      every strategy (unoptimized, fixed, greedy, beam) on every accepted
                 program, in parallel worker processes (deterministic per program)
  2. correctness every strategy's output is checked, independently of the search,
                 against (a) the manifest's fixture tests (expected outputs from the
                 AST reference) and (b) `--fresh` held-out random inputs evaluated by
                 the AST reference; plus the compiled backend on the same inputs
  3. ablation    greedy without M8 (instruction reordering), same protocol
  4. sensitivity greedy + fixed under every cost-weight configuration (cost.WEIGHT_CONFIGS)
  5. timing      wall-clock time of each strategy's program (interpreter and compiled
                 backends), run sequentially in the main process, interleaved order

Outputs (in --out, default results/final):
  per_program.csv      one row per (program, strategy): metrics, cost, correctness, search effort
  steps.jsonl          accepted transformation sequence per (program, strategy)
  sensitivity.csv      one row per (program, weight config, strategy)
  timing_raw.csv       every raw timing sample (ns per batch)
  timing.csv           per (program, strategy, backend): median / IQR / min per execution
  summary.json         aggregates by strategy x group x category, environment, config, provenance
All files except the timing ones and *_seconds fields are deterministic.
"""

import argparse
import csv
import hashlib
import json
import os
import platform
import random
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from execution.interpreter import CYCLES, STALL_CYCLES, run  # noqa: E402
from execution.pycodegen import compile_tac  # noqa: E402
from execution.reference import ast_run_detailed  # noqa: E402
from execution.timing import summarize, time_variants  # noqa: E402
from frontend.parser import parse  # noqa: E402
from frontend.tac import compile_source, format_tac  # noqa: E402
from optimizer.cost import WEIGHT_CONFIGS, WEIGHTS  # noqa: E402
from optimizer.mutations import OPERATORS  # noqa: E402
from optimizer.strategies import FIXED_ORDER, STRATEGIES  # noqa: E402

DEFAULT_MANIFEST = os.path.join(ROOT, "dataset", "miniarith-v1", "manifest.jsonl")
STRATEGY_NAMES = ("unoptimized", "fixed", "greedy", "beam")
METRICS = ("instructions", "arith_ops", "temps", "cycles")
SEARCH_CONFIG = {"validation_cases": 25, "validation_seed": 42, "validation_protocol": "v2 (structured vectors)",
                 "beam_width": 4, "beam_patience": 3,
                 "time_limit_seconds": 120, "fixed_order": list(FIXED_ORDER)}
NO_M8 = [(op, fn) for op, fn in OPERATORS if op != "M8"]


def rel(path):
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except ValueError:  # different drive on Windows
        return path


def load_manifest(path, limit=None, ids=None):
    with open(path, encoding="utf-8") as fh:
        recs = [json.loads(line) for line in fh]
    recs = [r for r in recs if r["status"] == "accepted"]
    if ids:
        recs = [r for r in recs if r["id"] in ids]
    if limit:
        # stratified: keep group/category proportions roughly by taking every k-th
        step = max(1, len(recs) // limit)
        recs = recs[::step][:limit]
    return recs


def fresh_inputs(n_inputs, n, seed):
    rng = random.Random(f"heldout:{seed}")
    cases = [tuple(rng.choice([0, 1, -1, 2, -3, 5]) for _ in range(n_inputs)) for _ in range(min(40, n))]
    cases += [tuple(rng.randint(-10 ** 6, 10 ** 6) for _ in range(n_inputs)) for _ in range(n - len(cases))]
    return cases


def check_correctness(rec, program, ast, fresh):
    """Independent of the search: fixture tests + held-out inputs vs the AST reference."""
    fixture_ok = 0
    for t in rec["tests"]:
        r = run(program, t["inputs"])
        if list(r.outputs) == t["expected_outputs"] and r.error == t["expected_error"]:
            fixture_ok += 1
    heldout_ok = 0
    compiled = compile_tac(program)
    compiled_ok = 0
    for case in fresh:
        ref = ast_run_detailed(ast, case).observable
        heldout_ok += run(program, case).observable == ref
        compiled_ok += compiled(case) == ref
    return fixture_ok, heldout_ok, compiled_ok


def run_one(task):
    """Worker: all strategies (+ ablation, + sensitivity) for one program."""
    rec, fresh_n, seed, do_sens = task
    program = compile_source(rec["source"])
    ast = parse(rec["source"])
    fresh = fresh_inputs(rec["n_inputs"], fresh_n, seed)
    common = dict(n_cases=SEARCH_CONFIG["validation_cases"], seed=SEARCH_CONFIG["validation_seed"],
                  time_limit=SEARCH_CONFIG["time_limit_seconds"], structured=True)
    rows, steps, sens, programs = [], [], [], {}
    variants = [(name, STRATEGIES[name], {}) for name in STRATEGY_NAMES]
    variants.append(("greedy_noM8", STRATEGIES["greedy"], {"operators": NO_M8}))
    for name, fn, extra in variants:
        kw = dict(common, **extra)
        if name == "beam":
            kw.update(width=SEARCH_CONFIG["beam_width"], patience=SEARCH_CONFIG["beam_patience"])
        try:
            res = fn(program, **kw)
        except Exception as exc:  # recorded, never hidden
            rows.append({"program_id": rec["id"], "strategy": name, "status": f"crash: {type(exc).__name__}: {exc}"})
            continue
        fix_ok, held_ok, comp_ok = check_correctness(rec, res.optimized, ast, fresh)
        b, f = res.base_metrics, res.final_metrics
        row = {"program_id": rec["id"], "group": rec["group"], "subgroup": rec["subgroup"],
               "category": rec["category"], "strategy": name, "status": res.status}
        for m in METRICS:
            row[f"{m}_before"] = round(getattr(b, m), 4)
            row[f"{m}_after"] = round(getattr(f, m), 4)
        row.update({
            "final_cost": round(res.final_cost, 6),
            "cost_reduction_pct": round((1 - res.final_cost) * 100, 3),
            "transformations": len(res.steps),
            "candidates_evaluated": res.candidates_evaluated,
            "candidates_valid": res.candidates_valid,
            "candidates_rejected": res.candidates_rejected,
            "programs_executed": res.programs_executed,
            "fixture_tests": len(rec["tests"]), "fixture_tests_passed": fix_ok,
            "heldout_inputs": len(fresh), "heldout_matching_reference": held_ok,
            "compiled_matching_reference": comp_ok,
            "correct": int(fix_ok == len(rec["tests"]) and held_ok == len(fresh) and comp_ok == len(fresh)),
            "search_seconds": round(res.seconds, 4),
        })
        rows.append(row)
        steps.append({"program_id": rec["id"], "strategy": name, "steps": res.steps,
                      "optimized_tac": format_tac(res.optimized, numbered=False)})
        programs[name] = res.optimized
    if do_sens:
        for cfg_name, weights in WEIGHT_CONFIGS.items():
            for name in ("fixed", "greedy"):
                res = STRATEGIES[name](program, weights=weights, **common)
                from optimizer.cost import cost as cost_fn  # baseline-weight view of the same program
                ok = check_correctness(rec, res.optimized, ast, fresh[:50])
                sens.append({"program_id": rec["id"], "group": rec["group"], "category": rec["category"],
                             "weights": cfg_name, "strategy": name,
                             "cost_under_config": round(res.final_cost, 6),
                             "cost_under_baseline_weights": round(cost_fn(res.final_metrics, res.base_metrics, WEIGHTS), 6),
                             **{f"{m}_after": round(getattr(res.final_metrics, m), 4) for m in METRICS},
                             **{f"{m}_before": round(getattr(res.base_metrics, m), 4) for m in METRICS},
                             "transformations": len(res.steps),
                             "correct": int(ok[0] == len(rec["tests"]) and ok[1] == 50 and ok[2] == 50)})
    return rec["id"], rows, steps, sens, {k: format_tac(v, numbered=False) for k, v in programs.items()}


def write_csv(path, rows):
    if not rows:
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def git_commit():
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True, timeout=10)
        return {"commit": out.stdout.strip() or None, "dirty_worktree": bool(dirty.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty_worktree": None}


def timing_phase(records, programs, args, log):
    """Sequential, interleaved wall-clock timing of every strategy's program."""
    from frontend.tac import parse_tac
    raw, summary = [], []
    names = list(STRATEGY_NAMES) + ["greedy_noM8"]
    for k, rec in enumerate(records):
        if rec["id"] not in programs:
            continue
        variants = {n: parse_tac(programs[rec["id"]][n]) if programs[rec["id"]][n] else ()
                    for n in names if n in programs[rec["id"]]}
        # timing batch: the error-free fixture inputs (identical for all variants)
        batch = [tuple(t["inputs"]) for t in rec["tests"] if t["expected_error"] is None] or \
                [tuple(t["inputs"]) for t in rec["tests"]]
        for backend in ("interp", "compiled"):
            samples = time_variants(variants, batch, backend=backend, warmup=args.warmup, repeats=args.repeats)
            for n, xs in samples.items():
                for i, x in enumerate(xs):
                    raw.append({"program_id": rec["id"], "strategy": n, "backend": backend, "repeat": i,
                                "batch_size": len(batch), "ns": x})
                s = summarize(xs, per=len(batch))
                summary.append({"program_id": rec["id"], "group": rec["group"], "category": rec["category"],
                                "strategy": n, "backend": backend, "batch_size": len(batch),
                                **{k2: round(v, 1) if isinstance(v, float) else v for k2, v in s.items()}})
        if (k + 1) % 50 == 0:
            log(f"    timed {k + 1}/{len(records)}")
    return raw, summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "final"))
    ap.add_argument("--limit", type=int, default=None, help="run on a stratified subset of N programs")
    ap.add_argument("--ids", nargs="*", default=None)
    ap.add_argument("--fresh", type=int, default=200, help="held-out inputs per program")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--skip-timing", action="store_true")
    ap.add_argument("--skip-sensitivity", action="store_true")
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--repeats", type=int, default=15)
    args = ap.parse_args(argv)

    from experiments.summarize import build_summary  # local import keeps worker start-up light

    t0 = time.perf_counter()
    records = load_manifest(args.manifest, args.limit, set(args.ids) if args.ids else None)
    with open(args.manifest, "rb") as fh:
        manifest_hash = hashlib.sha256(fh.read()).hexdigest()
    os.makedirs(args.out, exist_ok=True)
    print(f"{len(records)} accepted programs from {rel(args.manifest)}; "
          f"{args.workers} workers; output {rel(args.out)}")

    tasks = [(r, args.fresh, args.seed, not args.skip_sensitivity) for r in records]
    results = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for k, out in enumerate(pool.map(run_one, tasks, chunksize=2)):
            results[out[0]] = out
            if (k + 1) % 50 == 0 or k + 1 == len(tasks):
                print(f"  searched {k + 1}/{len(tasks)}  ({time.perf_counter() - t0:.0f}s)")
    rows, steps, sens, programs = [], [], [], {}
    for rec in records:
        _, r, s, se, progs = results[rec["id"]]
        rows += r
        steps += s
        sens += se
        programs[rec["id"]] = progs
    search_seconds = time.perf_counter() - t0

    write_csv(os.path.join(args.out, "per_program.csv"), rows)
    write_csv(os.path.join(args.out, "sensitivity.csv"), sens)
    with open(os.path.join(args.out, "steps.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        for s in steps:
            fh.write(json.dumps(s) + "\n")

    timing_rows = []
    if not args.skip_timing:
        print("  timing phase (sequential)")
        raw, timing_rows = timing_phase(records, programs, args, print)
        write_csv(os.path.join(args.out, "timing_raw.csv"), raw)
        write_csv(os.path.join(args.out, "timing.csv"), timing_rows)

    env = {"python": platform.python_version(), "implementation": platform.python_implementation(),
           "platform": platform.platform(), "processor": platform.processor(), "cpu_count": os.cpu_count(),
           **git_commit()}
    config = {**SEARCH_CONFIG, "heldout_inputs": args.fresh, "heldout_seed": args.seed,
              "cost_weights_baseline": WEIGHTS, "weight_configs": WEIGHT_CONFIGS,
              "cycle_model": {**CYCLES, "raw_stall": STALL_CYCLES},
              "timing": {"warmup": args.warmup, "repeats": args.repeats, "batch": "error-free fixture inputs",
                         "order": "interleaved, rotating start", "gc": "disabled while timing"},
              "operators": [op for op, _ in OPERATORS], "limit": args.limit}
    summary = build_summary(rows, sens, timing_rows, config=config, env=env,
                            dataset={"manifest": rel(args.manifest),
                                     "manifest_sha256": manifest_hash, "programs": len(records)},
                            seconds={"search_and_checks": round(search_seconds, 1),
                                     "total": round(time.perf_counter() - t0, 1)})
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    c = summary["correctness"]
    print(f"\n  correctness: {c['correct_rows']}/{c['rows']} (program, strategy) results correct; "
          f"crashes {c['crashes']}; timeouts {c['timeouts']}")
    for s, v in summary["overall"].items():
        print(f"  {s:<12} mean cost {v['mean_final_cost']:.4f}  median {v['median_final_cost']:.4f}  "
              f"evals {v['mean_candidates_evaluated']:.0f}  search {v['total_search_seconds']:.1f}s")
    print(f"  wrote {rel(args.out)}  ({time.perf_counter() - t0:.0f}s)")
    return 0 if c["correct_rows"] == c["rows"] and not c["crashes"] else 1


if __name__ == "__main__":
    sys.exit(main())
