"""Statistics and publication plots for the Review-1 dataset run.

    python run_dataset.py                       # produce the CSVs
    python -m visualization.dataset_report      # read them, write stats + plots

Reads ONLY results/results.csv and results/candidate_log.csv. Before plotting it
checks that the two files agree with each other and stops if they do not.

Writes:
    results/plots/1_baseline_vs_optimized_cost.png
    results/plots/2_cost_by_iteration.png
    results/plots/3_operator_frequency.png
    results/plots/4_valid_candidates_per_program.png
    results/statistics.json
"""

import argparse
import csv
import json
import os
import sys
from collections import OrderedDict, defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BLUE = "#2a78d6"
ORANGE = "#eb6834"
BASELINE_GRAY = "#b4b2a7"
TEXT = "#1f1f1e"
MUTED = "#6b6a63"
GRID = "#e4e3dc"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.titlesize": 11,
    "axes.labelsize": 9,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
})


# --- loading & consistency --------------------------------------------------------
def load(results_path, log_path):
    with open(results_path, newline="", encoding="utf-8") as fh:
        programs = list(csv.DictReader(fh))
    with open(log_path, newline="", encoding="utf-8") as fh:
        cands = list(csv.DictReader(fh))
    for r in programs:
        for k in ("final_cost", "cost_reduction_pct"):
            r[k] = float(r[k])
        for k in ("iterations", "candidates_evaluated", "candidates_valid", "candidates_rejected",
                  "n_dataset_tests", "dataset_tests_passed_optimized", "dataset_tests_passed_original",
                  "fresh_inputs_checked", "fresh_inputs_matching_reference"):
            r[k] = int(r[k])
    for c in cands:
        for k in ("iteration", "candidate_index", "valid", "accepted", "improves_current"):
            c[k] = int(c[k])
        c["cost"] = float(c["cost"]) if c["cost"] else None
    return programs, cands


def check_consistency(programs, cands):
    by_prog = defaultdict(list)
    for c in cands:
        by_prog[c["program_id"]].append(c)
    errors = []
    for r in programs:
        cs = by_prog[r["program_id"]]
        acc = sorted((c for c in cs if c["accepted"]), key=lambda c: c["iteration"])
        if len(cs) != r["candidates_evaluated"]:
            errors.append(f"{r['program_id']}: log has {len(cs)} candidates, results.csv says {r['candidates_evaluated']}")
        if sum(c["valid"] for c in cs) != r["candidates_valid"]:
            errors.append(f"{r['program_id']}: valid count mismatch")
        if len(acc) != r["iterations"]:
            errors.append(f"{r['program_id']}: {len(acc)} accepted in log vs {r['iterations']} iterations")
        if acc and abs(acc[-1]["cost"] - r["final_cost"]) > 1e-6:
            errors.append(f"{r['program_id']}: last accepted cost {acc[-1]['cost']} != final_cost {r['final_cost']}")
    if set(by_prog) != {r["program_id"] for r in programs}:
        errors.append("program ids differ between the two files")
    return errors


# --- statistics ---------------------------------------------------------------------
def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs)


def compute_stats(programs, cands):
    best = max(programs, key=lambda r: r["cost_reduction_pct"])
    worst = min(programs, key=lambda r: r["cost_reduction_pct"])
    rounds = defaultdict(int)
    for c in cands:
        rounds[c["program_id"]] = max(rounds[c["program_id"]], c["iteration"])

    ops = OrderedDict()
    for c in sorted(cands, key=lambda c: c["operator"]):
        o = ops.setdefault(c["operator"], {"name": c["operator_name"], "proposed": 0, "valid": 0, "accepted": 0,
                                           "programs_applied_in": set()})
        o["proposed"] += 1
        o["valid"] += c["valid"]
        o["accepted"] += c["accepted"]
        if c["accepted"]:
            o["programs_applied_in"].add(c["program_id"])
    total_accepted = sum(o["accepted"] for o in ops.values())
    for o in ops.values():
        o["hit_rate_pct"] = round(100 * o["accepted"] / o["proposed"], 2)  # accepted / proposed
        o["share_of_accepted_pct"] = round(100 * o["accepted"] / total_accepted, 2)
        o["programs_applied_in"] = len(o["programs_applied_in"])

    n_tests = sum(r["n_dataset_tests"] for r in programs)
    n_fresh = sum(r["fresh_inputs_checked"] for r in programs)
    return {
        "source_files": ["results/results.csv", "results/candidate_log.csv"],
        "programs": len(programs),
        "average_cost_reduction_pct": round(mean(r["cost_reduction_pct"] for r in programs), 2),
        "maximum_cost_reduction_pct": round(best["cost_reduction_pct"], 2),
        "maximum_cost_reduction_program": best["program_id"],
        "minimum_cost_reduction_pct": round(worst["cost_reduction_pct"], 2),
        "minimum_cost_reduction_program": worst["program_id"],
        "average_final_cost": round(mean(r["final_cost"] for r in programs), 4),
        "average_candidates_explored": round(mean(r["candidates_evaluated"] for r in programs), 2),
        "total_candidates_explored": len(cands),
        "average_iterations_accepted_mutations": round(mean(r["iterations"] for r in programs), 2),
        "average_search_rounds_incl_convergence_check": round(mean(rounds.values()), 2),
        "candidate_validity_rate_pct": round(100 * sum(c["valid"] for c in cands) / len(cands), 2),
        "candidates_rejected": sum(1 - c["valid"] for c in cands),
        "candidate_acceptance_rate_pct": round(100 * total_accepted / len(cands), 2),
        "correctness": {
            "dataset_tests_passed_optimized": sum(r["dataset_tests_passed_optimized"] for r in programs),
            "dataset_tests_total": n_tests,
            "dataset_test_pass_rate_pct": round(100 * sum(r["dataset_tests_passed_optimized"] for r in programs) / n_tests, 2),
            "fresh_inputs_matching_reference": sum(r["fresh_inputs_matching_reference"] for r in programs),
            "fresh_inputs_checked": n_fresh,
            "fresh_input_agreement_pct": round(100 * sum(r["fresh_inputs_matching_reference"] for r in programs) / n_fresh, 2),
            "programs_fully_correct": sum(r["dataset_tests_passed_optimized"] == r["n_dataset_tests"]
                                          and r["fresh_inputs_matching_reference"] == r["fresh_inputs_checked"]
                                          for r in programs),
        },
        "operator_hit_frequency": ops,
    }


# --- plots --------------------------------------------------------------------------
def style(ax, grid_axis="y"):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, length=0)
    getattr(ax, f"{grid_axis}axis").grid(True, color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)


def category_brackets(ax, programs, y):
    """Category names under groups of consecutive program ids."""
    start = 0
    for i in range(1, len(programs) + 1):
        if i == len(programs) or programs[i]["category"] != programs[start]["category"]:
            mid = (start + i - 1) / 2
            ax.annotate(programs[start]["category"], (mid, y), xycoords=("data", "axes fraction"),
                        ha="center", va="top", fontsize=7.5, color=MUTED)
            if i < len(programs):
                ax.axvline(i - 0.5, color=GRID, linewidth=0.7, zorder=0)
            start = i


def plot_baseline_vs_optimized(programs, path):
    fig, ax = plt.subplots(figsize=(10, 4.2))
    x = range(len(programs))
    w = 0.38
    ax.bar([i - w / 2 - 0.01 for i in x], [1.0] * len(programs), w, color=BASELINE_GRAY, label="Baseline (naive TAC)")
    bars = ax.bar([i + w / 2 + 0.01 for i in x], [r["final_cost"] for r in programs], w, color=BLUE, label="Optimised")
    for bar, r in zip(bars, programs):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.015, f"{r['final_cost']:.2f}",
                ha="center", va="bottom", fontsize=7, color=TEXT)
    style(ax)
    ax.set_xticks(list(x))
    ax.set_xticklabels([r["program_id"] for r in programs])
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("Normalised cost (baseline = 1.0)", color=TEXT)
    ax.set_title("Baseline vs optimised cost, 15 programs", loc="left", color=TEXT)
    ax.legend(frameon=False, ncol=2, loc="upper right", bbox_to_anchor=(1, 1.1), fontsize=8)
    category_brackets(ax, programs, -0.09)
    fig.savefig(path)
    plt.close(fig)


def plot_cost_by_iteration(programs, cands, path):
    traj = defaultdict(lambda: [1.0])
    for c in sorted((c for c in cands if c["accepted"]), key=lambda c: (c["program_id"], c["iteration"])):
        traj[c["program_id"]].append(c["cost"])
    cats = list(OrderedDict.fromkeys(r["category"] for r in programs))
    max_iter = max(len(t) for t in traj.values()) - 1
    fig, axes = plt.subplots(2, 3, figsize=(10, 5.6), sharex=True, sharey=True)
    for ax, cat in zip(axes.flat, cats):
        members = [r for r in programs if r["category"] == cat]
        for other in programs:  # faint context: every other program
            if other["category"] != cat:
                t = traj[other["program_id"]]
                ax.plot(range(len(t)), t, color=GRID, linewidth=0.8, zorder=1)
        label_x = max(len(traj[r["program_id"]]) for r in members) + 1
        label_y, min_gap = {}, 0.07  # dodge labels whose endpoints nearly coincide
        for r in sorted(members, key=lambda r: traj[r["program_id"]][-1]):
            y = traj[r["program_id"]][-1]
            if label_y and y - max(label_y.values()) < min_gap:
                y = max(label_y.values()) + min_gap
            label_y[r["program_id"]] = y
        for r in members:
            t = traj[r["program_id"]]
            end = (len(t) - 1, t[-1])
            ax.plot(range(len(t)), t, color=BLUE, linewidth=2, zorder=3)
            ax.plot(*end, "o", color=BLUE, markersize=4, zorder=4, markeredgecolor="white", markeredgewidth=1)
            ax.annotate(f"{r['program_id']} {t[-1]:.2f}", end, xytext=(label_x, label_y[r["program_id"]]),
                        textcoords="data", va="center", fontsize=7, color=TEXT,
                        arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=0.6, shrinkA=0, shrinkB=2))
        style(ax)
        ax.set_title(cat, loc="left", fontsize=9, color=TEXT)
        ax.set_xlim(0, max_iter + 7)
        ax.set_ylim(0, 1.05)
    for ax in axes[1]:
        ax.set_xlabel("Accepted mutation (iteration)", color=TEXT)
    for ax in axes[:, 0]:
        ax.set_ylabel("Normalised cost", color=TEXT)
    fig.suptitle("Cost reduction across optimisation iterations (grey = other categories)",
                 x=0.01, ha="left", color=TEXT, fontsize=11)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def plot_operator_frequency(stats, path):
    ops = sorted(stats["operator_hit_frequency"].items(), key=lambda kv: kv[1]["accepted"])
    labels = [f"{op}  {o['name']}" for op, o in ops]
    fig, ax = plt.subplots(figsize=(8.5, 0.42 * len(ops) + 1.3))
    bars = ax.barh(labels, [o["accepted"] for _, o in ops], color=BLUE, height=0.62)
    for bar, (_, o) in zip(bars, ops):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                f"{o['accepted']}  ({o['hit_rate_pct']:.0f}% of {o['proposed']} proposed)",
                va="center", fontsize=7.5, color=TEXT)
    style(ax, grid_axis="x")
    ax.set_xlim(0, max(o["accepted"] for _, o in ops) * 1.45)
    ax.set_xlabel("Mutations accepted (all 15 programs)", color=TEXT)
    ax.set_title("Mutation operator frequency", loc="left", color=TEXT)
    fig.savefig(path)
    plt.close(fig)


def plot_valid_candidates(programs, path):
    fig, ax = plt.subplots(figsize=(10, 4.2))
    x = list(range(len(programs)))
    valid = [r["candidates_valid"] for r in programs]
    rejected = [r["candidates_rejected"] for r in programs]
    ax.bar(x, valid, 0.62, color=BLUE, label="Valid (passed validation)")
    ax.bar(x, rejected, 0.62, bottom=valid, color=ORANGE, label="Rejected by validation",
           edgecolor="white", linewidth=1)
    for i, (v, rj) in enumerate(zip(valid, rejected)):
        ax.text(i, v + rj + 6, f"{v}" + (f"+{rj}" if rj else ""), ha="center", va="bottom", fontsize=7, color=TEXT)
    style(ax)
    ax.set_xticks(x)
    ax.set_xticklabels([r["program_id"] for r in programs])
    ax.set_ylim(0, max(v + r for v, r in zip(valid, rejected)) * 1.12)
    ax.set_ylabel("Candidates evaluated", color=TEXT)
    total_rej = sum(rejected)
    ax.set_title(f"Valid candidates per program ({total_rej} rejected in total)", loc="left", color=TEXT)
    ax.legend(frameon=False, ncol=2, loc="upper right", bbox_to_anchor=(1, 1.1), fontsize=8)
    category_brackets(ax, programs, -0.09)
    fig.savefig(path)
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dataset statistics and plots from the result CSVs")
    parser.add_argument("--results", default=os.path.join(ROOT, "results", "results.csv"))
    parser.add_argument("--log", default=os.path.join(ROOT, "results", "candidate_log.csv"))
    parser.add_argument("--out", default=os.path.join(ROOT, "results"))
    args = parser.parse_args(argv)

    programs, cands = load(args.results, args.log)
    errors = check_consistency(programs, cands)
    if errors:
        print("results.csv and candidate_log.csv are inconsistent - re-run `python run_dataset.py`:")
        print("\n".join(f"  {e}" for e in errors))
        return 1

    stats = compute_stats(programs, cands)
    plot_dir = os.path.join(args.out, "plots")
    os.makedirs(plot_dir, exist_ok=True)
    paths = [os.path.join(plot_dir, name) for name in (
        "1_baseline_vs_optimized_cost.png", "2_cost_by_iteration.png",
        "3_operator_frequency.png", "4_valid_candidates_per_program.png")]
    plot_baseline_vs_optimized(programs, paths[0])
    plot_cost_by_iteration(programs, cands, paths[1])
    plot_operator_frequency(stats, paths[2])
    plot_valid_candidates(programs, paths[3])
    with open(os.path.join(args.out, "statistics.json"), "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2)

    c = stats["correctness"]
    print(f"consistency check: results.csv and candidate_log.csv agree ({len(programs)} programs, {len(cands)} candidates)\n")
    print(f"average cost reduction        {stats['average_cost_reduction_pct']:.2f}%")
    print(f"maximum cost reduction        {stats['maximum_cost_reduction_pct']:.2f}%  ({stats['maximum_cost_reduction_program']})")
    print(f"minimum cost reduction        {stats['minimum_cost_reduction_pct']:.2f}%  ({stats['minimum_cost_reduction_program']})")
    print(f"average candidates explored   {stats['average_candidates_explored']:.2f}  (total {stats['total_candidates_explored']})")
    print(f"average iterations            {stats['average_iterations_accepted_mutations']:.2f} accepted mutations "
          f"({stats['average_search_rounds_incl_convergence_check']:.2f} search rounds incl. final convergence check)")
    print(f"candidate validity rate       {stats['candidate_validity_rate_pct']:.2f}%  ({stats['candidates_rejected']} rejected)")
    print(f"candidate acceptance rate     {stats['candidate_acceptance_rate_pct']:.2f}%")
    print(f"correctness                   {c['dataset_tests_passed_optimized']}/{c['dataset_tests_total']} dataset tests "
          f"({c['dataset_test_pass_rate_pct']:.2f}%), {c['fresh_inputs_matching_reference']}/{c['fresh_inputs_checked']} "
          f"unseen inputs ({c['fresh_input_agreement_pct']:.2f}%), {c['programs_fully_correct']}/{len(programs)} programs fully correct")
    print("\nmutation hit frequency by operator")
    print(f"  {'op':<3} {'name':<33}{'proposed':>9}{'valid':>7}{'accepted':>9}{'hit rate':>10}{'share':>8}{'programs':>9}")
    for op, o in stats["operator_hit_frequency"].items():
        print(f"  {op:<3} {o['name']:<33}{o['proposed']:>9}{o['valid']:>7}{o['accepted']:>9}"
              f"{o['hit_rate_pct']:>9.2f}%{o['share_of_accepted_pct']:>7.2f}%{o['programs_applied_in']:>6}/15")
    print()
    for p in paths + [os.path.join(args.out, "statistics.json")]:
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
