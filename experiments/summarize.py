"""Aggregate experiment rows into summary.json (no plotting, no I/O besides return).

Can also be run standalone to rebuild summary.json from saved CSVs:
    python -m experiments.summarize results/final
"""

import csv
import json
import math
import os
import statistics
import sys
from collections import defaultdict

METRICS = ("instructions", "arith_ops", "temps", "cycles")
TIE = 1e-9


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return x


def pct(before, after):
    return (after - before) / before * 100 if before else 0.0


def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def describe(rows):
    costs = [r["final_cost"] for r in rows]
    out = {
        "programs": len(rows),
        "mean_final_cost": round(statistics.fmean(costs), 6),
        "median_final_cost": round(statistics.median(costs), 6),
        "p10_final_cost": round(quantile(costs, 0.1), 6),
        "p90_final_cost": round(quantile(costs, 0.9), 6),
        "mean_cost_reduction_pct": round(statistics.fmean(r["cost_reduction_pct"] for r in rows), 3),
        "programs_improved": sum(r["final_cost"] < 1 - TIE for r in rows),
        "programs_worse_than_unoptimized": sum(r["final_cost"] > 1 + TIE for r in rows),
        "mean_transformations": round(statistics.fmean(r["transformations"] for r in rows), 3),
        "mean_candidates_evaluated": round(statistics.fmean(r["candidates_evaluated"] for r in rows), 2),
        "total_candidates_evaluated": int(sum(r["candidates_evaluated"] for r in rows)),
        "total_candidates_rejected": int(sum(r["candidates_rejected"] for r in rows)),
        "mean_programs_executed": round(statistics.fmean(r["programs_executed"] for r in rows), 2),
        "total_search_seconds": round(sum(r["search_seconds"] for r in rows), 3),
        "correct": int(sum(r["correct"] for r in rows)),
    }
    for m in METRICS:
        out[f"mean_{m}_change_pct"] = round(statistics.fmean(pct(r[f"{m}_before"], r[f"{m}_after"]) for r in rows), 3)
        out[f"total_{m}_before"] = round(sum(r[f"{m}_before"] for r in rows), 2)
        out[f"total_{m}_after"] = round(sum(r[f"{m}_after"] for r in rows), 2)
    return out


def compare(rows_by_strategy, a, b, key="final_cost"):
    """Per-program win/tie/loss of strategy a against b (lower is better)."""
    A = {r["program_id"]: r for r in rows_by_strategy.get(a, [])}
    B = {r["program_id"]: r for r in rows_by_strategy.get(b, [])}
    common = sorted(set(A) & set(B))
    if not common:
        return None
    diffs = [A[p][key] - B[p][key] for p in common]
    return {"a": a, "b": b, "metric": key, "programs": len(common),
            "a_better": sum(d < -TIE for d in diffs), "tie": sum(abs(d) <= TIE for d in diffs),
            "a_worse": sum(d > TIE for d in diffs), "mean_difference": round(statistics.fmean(diffs), 6),
            "max_a_better": round(min(diffs), 6), "max_a_worse": round(max(diffs), 6),
            "a_worse_programs": [p for p, d in zip(common, diffs) if d > TIE][:50]}


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 3:
        return None
    rx, ry = ranks(x), ranks(y)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return round(num / den, 4) if den else None


def timing_summary(timing_rows, rows):
    if not timing_rows:
        return None
    t = defaultdict(dict)  # (program, backend) -> strategy -> row
    for r in timing_rows:
        t[(r["program_id"], r["backend"])][r["strategy"]] = r
    metrics = {(r["program_id"], r["strategy"]): r for r in rows}
    out = {"classification_rule": "improved: strategy Q3 < unoptimized Q1 (non-overlapping IQRs); "
                                  "regressed: strategy Q1 > unoptimized Q3; otherwise inconclusive"}
    for backend in ("interp", "compiled"):
        per = {}
        for strat in ("fixed", "greedy", "beam", "greedy_noM8"):
            ratios, cls = [], defaultdict(int)
            groups = defaultdict(list)
            for (pid, be), d in t.items():
                if be != backend or strat not in d or "unoptimized" not in d:
                    continue
                u, s = d["unoptimized"], d[strat]
                ratio = u["median_ns"] / s["median_ns"] if s["median_ns"] else None
                if ratio:
                    ratios.append(ratio)
                    groups[u["group"]].append(ratio)
                if s["q3_ns"] < u["q1_ns"]:
                    cls["improved"] += 1
                elif s["q1_ns"] > u["q3_ns"]:
                    cls["regressed"] += 1
                else:
                    cls["inconclusive"] += 1
            if not ratios:
                continue
            per[strat] = {
                "programs": len(ratios),
                "geomean_speedup_vs_unoptimized": round(math.exp(statistics.fmean(math.log(x) for x in ratios)), 4),
                "median_speedup_vs_unoptimized": round(statistics.median(ratios), 4),
                "p10_speedup": round(quantile(ratios, 0.1), 4), "p90_speedup": round(quantile(ratios, 0.9), 4),
                **{k: cls[k] for k in ("improved", "inconclusive", "regressed")},
                "geomean_speedup_by_group": {g: round(math.exp(statistics.fmean(math.log(x) for x in v)), 4)
                                             for g, v in sorted(groups.items())},
            }
        # do the simulated metrics predict measured time? (all program x strategy points)
        xs_c, xs_i, ys = [], [], []
        for (pid, be), d in t.items():
            if be != backend:
                continue
            for strat, r in d.items():
                m = metrics.get((pid, strat))
                if m:
                    xs_c.append(m["cycles_after"])
                    xs_i.append(m["instructions_after"])
                    ys.append(r["median_ns"])
        # within-program view: does the cheaper (simulated) variant also run faster?
        agree = total = 0
        for (pid, be), d in t.items():
            if be != backend or "unoptimized" not in d:
                continue
            g, n = d.get("greedy"), d.get("greedy_noM8")
            mg, mn = metrics.get((pid, "greedy")), metrics.get((pid, "greedy_noM8"))
            if g and n and mg and mn and abs(mg["cycles_after"] - mn["cycles_after"]) > TIE:
                total += 1
                agree += (mg["cycles_after"] < mn["cycles_after"]) == (g["median_ns"] < n["median_ns"])
        out[backend] = {
            "per_strategy": per,
            "spearman_cycles_vs_time": spearman(xs_c, ys),
            "spearman_instructions_vs_time": spearman(xs_i, ys),
            "points": len(ys),
            "reordering_check": {
                "description": "programs where greedy and greedy_noM8 differ in simulated cycles: how often the "
                               "variant with fewer simulated cycles also has the lower measured median time",
                "programs": total, "agreements": agree,
                "agreement_rate": round(agree / total, 4) if total else None},
            "median_ns_per_execution": {s: round(statistics.median(d[s]["median_ns"] for (p, be), d in t.items()
                                                                     if be == backend and s in d), 1)
                                        for s in ("unoptimized", "fixed", "greedy", "beam", "greedy_noM8")},
        }
    return out


def sensitivity_summary(sens):
    if not sens:
        return None
    by = defaultdict(lambda: defaultdict(list))
    for r in sens:
        by[r["weights"]][r["strategy"]].append(r)
    out = {}
    base_sign = None
    for cfg in by:
        f = {r["program_id"]: r for r in by[cfg]["fixed"]}
        g = {r["program_id"]: r for r in by[cfg]["greedy"]}
        common = sorted(set(f) & set(g))
        diffs = [g[p]["cost_under_config"] - f[p]["cost_under_config"] for p in common]
        entry = {
            "programs": len(common),
            "fixed_mean_cost": round(statistics.fmean(f[p]["cost_under_config"] for p in common), 6),
            "greedy_mean_cost": round(statistics.fmean(g[p]["cost_under_config"] for p in common), 6),
            "greedy_mean_cost_under_baseline_weights": round(statistics.fmean(g[p]["cost_under_baseline_weights"] for p in common), 6),
            "greedy_better": sum(d < -TIE for d in diffs), "tie": sum(abs(d) <= TIE for d in diffs),
            "greedy_worse": sum(d > TIE for d in diffs),
            "greedy_mean_instructions_change_pct": round(statistics.fmean(
                pct(g[p]["instructions_before"], g[p]["instructions_after"]) for p in common), 3),
            "greedy_mean_cycles_change_pct": round(statistics.fmean(
                pct(g[p]["cycles_before"], g[p]["cycles_after"]) for p in common), 3),
            "greedy_mean_temps_change_pct": round(statistics.fmean(
                pct(g[p]["temps_before"], g[p]["temps_after"]) for p in common), 3),
            "correct": sum(r["correct"] for r in by[cfg]["fixed"] + by[cfg]["greedy"]),
            "results": len(by[cfg]["fixed"]) + len(by[cfg]["greedy"]),
        }
        sign = (entry["greedy_better"] > entry["greedy_worse"]) - (entry["greedy_better"] < entry["greedy_worse"])
        entry["greedy_vs_fixed_direction"] = {1: "greedy better on more programs", 0: "balanced/tied",
                                              -1: "fixed better on more programs"}[sign]
        if cfg == "baseline":
            base_sign = sign
        entry["_sign"] = sign
        out[cfg] = entry
    for entry in out.values():
        entry["direction_same_as_baseline"] = entry.pop("_sign") == base_sign
    return out


def build_summary(rows, sens, timing_rows, config=None, env=None, dataset=None, seconds=None):
    ok_rows = [r for r in rows if not str(r.get("status", "")).startswith("crash")]
    by_strategy = defaultdict(list)
    for r in ok_rows:
        by_strategy[r["strategy"]].append(r)
    by_group = defaultdict(lambda: defaultdict(list))
    by_cat = defaultdict(lambda: defaultdict(list))
    for r in ok_rows:
        by_group[r["strategy"]][r["group"]].append(r)
        by_cat[r["strategy"]][f"{r['group']}/{r['category']}"].append(r)
    return {
        "dataset": dataset, "config": config, "environment": env, "seconds": seconds,
        "determinism_note": "All fields are deterministic except environment, seconds, *_seconds and timing.",
        "correctness": {
            "rows": len(rows), "correct_rows": int(sum(r.get("correct", 0) for r in ok_rows)),
            "crashes": len(rows) - len(ok_rows),
            "timeouts": sum(r.get("status") == "timeout" for r in ok_rows),
            "budget_stops": sum(r.get("status") == "budget" for r in ok_rows),
            "by_strategy": {s: {"results": len(v), "correct": int(sum(r["correct"] for r in v)),
                                "fixture_tests": int(sum(r["fixture_tests"] for r in v)),
                                "fixture_tests_passed": int(sum(r["fixture_tests_passed"] for r in v)),
                                "heldout_checks": int(sum(r["heldout_inputs"] for r in v)),
                                "heldout_matching": int(sum(r["heldout_matching_reference"] for r in v)),
                                "compiled_matching": int(sum(r["compiled_matching_reference"] for r in v)),
                                "candidates_rejected_by_validator": int(sum(r["candidates_rejected"] for r in v))}
                            for s, v in by_strategy.items()},
        },
        "overall": {s: describe(v) for s, v in by_strategy.items()},
        "by_group": {s: {g: describe(v) for g, v in sorted(d.items())} for s, d in by_group.items()},
        "by_category": {s: {c: describe(v) for c, v in sorted(d.items())} for s, d in by_cat.items()},
        "comparisons": [c for c in (
            compare(by_strategy, "greedy", "fixed"),
            compare(by_strategy, "beam", "greedy"),
            compare(by_strategy, "beam", "fixed"),
            compare(by_strategy, "greedy", "greedy_noM8"),
            compare(by_strategy, "greedy", "greedy_noM8", key="cycles_after"),
            compare(by_strategy, "greedy", "fixed", key="instructions_after"),
            compare(by_strategy, "greedy", "fixed", key="cycles_after"),
        ) if c],
        "comparisons_by_group": {g: [c for c in (
            compare({s: [r for r in v if r["group"] == g] for s, v in by_strategy.items()}, "greedy", "fixed"),
            compare({s: [r for r in v if r["group"] == g] for s, v in by_strategy.items()}, "beam", "greedy"),
        ) if c] for g in sorted({r["group"] for r in ok_rows})},
        "sensitivity": sensitivity_summary(sens),
        "timing": timing_summary(timing_rows, ok_rows),
    }


def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [{k: _num(v) if k not in ("program_id", "group", "subgroup", "category", "strategy", "status",
                                         "weights", "backend") else v for k, v in r.items()}
                for r in csv.DictReader(fh)]


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    out = argv[0] if argv else "results/final"
    rows = load_csv(os.path.join(out, "per_program.csv"))
    sens = load_csv(os.path.join(out, "sensitivity.csv"))
    timing = load_csv(os.path.join(out, "timing.csv"))
    with open(os.path.join(out, "summary.json"), encoding="utf-8") as fh:
        old = json.load(fh)
    summary = build_summary(rows, sens, timing, old.get("config"), old.get("environment"), old.get("dataset"),
                            old.get("seconds"))
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    print(f"rebuilt {out}/summary.json from CSVs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
