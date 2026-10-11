"""Publication plots, generated ONLY from saved experiment logs.

    python -m experiments.plots                    # results/final -> results/final/plots
    python -m experiments.plots results/smoke

Reads per_program.csv, sensitivity.csv, timing.csv, summary.json, validator_eval.json
and dataset/miniarith-v1/statistics.json. Writes PNGs plus plots/index.json, which
maps every figure to the exact data file(s) it was drawn from.
"""

import csv
import json
import math
import os
import statistics
import sys
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# reference categorical palette (fixed slot order) + neutral baseline
COLORS = {"unoptimized": "#b4b2a7", "greedy": "#2a78d6", "fixed": "#eb6834", "beam": "#1baf7a",
          "greedy_noM8": "#eda100"}
NAMES = {"unoptimized": "Unoptimized", "greedy": "Greedy", "fixed": "Fixed pipeline", "beam": "Beam (w=4)",
         "greedy_noM8": "Greedy w/o M8"}
GROUPS = {"controlled": "A: controlled", "randomized": "B: randomized", "external": "C: external"}
TEXT, MUTED, GRID = "#1f1f1e", "#6b6a63", "#e4e3dc"
STATUS = {"rejected": "#2a78d6", "accepted_equivalent": "#b4b2a7", "accepted_but_different": "#d03b3b"}

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 11, "axes.labelsize": 9,
    "axes.edgecolor": "#b4b2a7", "axes.labelcolor": TEXT, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
    "savefig.dpi": 200, "savefig.bbox": "tight", "figure.facecolor": "white",
})


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [{k: (_num(v) if k not in ("program_id", "group", "subgroup", "category", "strategy", "status",
                                          "weights", "backend") else v) for k, v in r.items()} for r in csv.DictReader(fh)]


def read_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def caption(fig, text, y=-0.06):
    """Source/explanation line below the figure (placed under rotated tick labels)."""
    fig.text(0.0, y, text, ha="left", va="top", fontsize=7.5, color=MUTED, wrap=True)


def suptitle(fig, text):
    fig.suptitle(text, x=0.01, y=1.04, ha="left", fontweight="bold")


class Plotter:
    def __init__(self, res_dir):
        self.res = res_dir
        self.out = os.path.join(res_dir, "plots")
        os.makedirs(self.out, exist_ok=True)
        self.index = {}

    def save(self, fig, name, sources, description):
        path = os.path.join(self.out, name)
        fig.savefig(path)
        plt.close(fig)
        self.index[name] = {"sources": sources, "description": description}
        return path


def plot_all(res_dir):
    p = Plotter(res_dir)
    rows = read_csv(os.path.join(res_dir, "per_program.csv"))
    rows = [r for r in rows if not str(r.get("status", "")).startswith("crash")]
    sens = read_csv(os.path.join(res_dir, "sensitivity.csv"))
    timing = read_csv(os.path.join(res_dir, "timing.csv"))
    summary = read_json(os.path.join(res_dir, "summary.json"))
    veval = read_json(os.path.join(res_dir, "validator_eval.json"))
    dstats = read_json(os.path.join(ROOT, "dataset", "miniarith-v1", "statistics.json"))
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by[r["strategy"]][r["group"]].append(r)
    groups = [g for g in GROUPS if any(by[s][g] for s in by)]
    strategies = ["fixed", "greedy", "beam"]

    # 1. final-cost distribution per group (box plots)
    if rows:
        fig, axes = plt.subplots(1, len(groups), figsize=(3.2 * len(groups), 3.3), sharey=True)
        axes = [axes] if len(groups) == 1 else axes
        for ax, g in zip(axes, groups):
            data = [[r["final_cost"] for r in by[s][g]] for s in strategies]
            bp = ax.boxplot(data, patch_artist=True, widths=0.55, medianprops={"color": TEXT, "linewidth": 1.2},
                            flierprops={"markersize": 3, "markeredgecolor": MUTED})
            for patch, s in zip(bp["boxes"], strategies):
                patch.set_facecolor(COLORS[s])
                patch.set_alpha(0.85)
                patch.set_edgecolor("white")
            ax.set_xticks(range(1, 4), [NAMES[s] for s in strategies], rotation=20)
            ax.set_title(f"{GROUPS[g]} (n={len(by['greedy'][g])})")
            ax.axhline(1.0, color=MUTED, linewidth=0.8)
        axes[0].set_ylabel("final cost (unoptimized = 1.0; lower is better)")
        fig.suptitle("Final simulated cost per program, by dataset group")
        caption(fig, y=-0.1, text="Box: interquartile range, line: median, whiskers: 1.5 IQR. Simulated weighted cost "
                     "0.4 I + 0.3 A + 0.2 T + 0.1 C, normalised per program. Source: per_program.csv.")
        p.save(fig, "01_cost_distribution_by_group.png", ["per_program.csv"], "final cost box plots per group")

    # 2. mean % change per metric
    if rows:
        metrics = [("instructions", "TAC instructions"), ("arith_ops", "arithmetic ops"), ("temps", "temporaries"),
                   ("cycles", "simulated cycles")]
        fig, ax = plt.subplots(figsize=(7, 3.2))
        width = 0.24
        for j, s in enumerate(strategies):
            vals = []
            for m, _ in metrics:
                xs = [(r[f"{m}_after"] - r[f"{m}_before"]) / r[f"{m}_before"] * 100 for g in groups for r in by[s][g]
                      if r[f"{m}_before"]]
                vals.append(statistics.fmean(xs))
            xs = [i + (j - 1) * width for i in range(len(metrics))]
            ax.bar(xs, vals, width - 0.03, color=COLORS[s], label=NAMES[s])
            for x, v in zip(xs, vals):
                ax.text(x, v - 1.5, f"{v:.0f}%", ha="center", va="top", fontsize=7, color=TEXT)
        ax.set_xticks(range(len(metrics)), [m[1] for m in metrics])
        ax.set_ylabel("mean change per program (%)")
        ax.axhline(0, color=MUTED, linewidth=0.8)
        ax.set_ylim(min(ax.get_ylim()[0], -80), 0)
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3)
        ax.set_title("Mean per-program change vs. unoptimized TAC (all groups)", loc="left", fontweight="bold")
        caption(fig, y=-0.16, text="Negative = reduction. Simulated cycles come from the deterministic cycle model, not hardware. "
                     "Source: per_program.csv.")
        p.save(fig, "02_metric_changes.png", ["per_program.csv"], "mean % change of each metric per strategy")

    # 3. category costs
    if summary:
        cats = list(summary["by_category"]["greedy"])
        fig, ax = plt.subplots(figsize=(8.5, 3.4))
        width = 0.26
        for j, s in enumerate(strategies):
            vals = [summary["by_category"][s][c]["mean_final_cost"] for c in cats]
            ax.bar([i + (j - 1) * width for i in range(len(cats))], vals, width - 0.03, color=COLORS[s], label=NAMES[s])
        ax.set_xticks(range(len(cats)), [c.replace("/", "\n") for c in cats], fontsize=7.5)
        ax.set_ylabel("mean final cost")
        ax.set_ylim(0, 1.05)
        ax.legend(ncol=3, loc="upper left")
        ax.set_title("Mean final cost by group / category", loc="left", fontweight="bold")
        caption(fig, "Group/category labels; groups are never pooled. Source: summary.json (by_category).")
        p.save(fig, "03_category_costs.png", ["summary.json"], "mean final cost by category")

    # 4. greedy vs fixed / beam vs greedy per program
    if rows:
        fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.5))
        for ax, (a, b) in zip(axes, (("fixed", "greedy"), ("greedy", "beam"))):
            A = {r["program_id"]: r for g in groups for r in by[a][g]}
            B = {r["program_id"]: r for g in groups for r in by[b][g]}
            common = sorted(set(A) & set(B))
            xs, ys = [A[k]["final_cost"] for k in common], [B[k]["final_cost"] for k in common]
            better = sum(y < x - 1e-9 for x, y in zip(xs, ys))
            worse = sum(y > x + 1e-9 for x, y in zip(xs, ys))
            ax.scatter(xs, ys, s=12, color=COLORS[b], alpha=0.7, edgecolors="white", linewidths=0.4)
            ax.plot([0, 1], [0, 1], color=MUTED, linewidth=0.8)
            ax.set_xlim(0, 1.02)
            ax.set_ylim(0, 1.02)
            ax.set_xlabel(f"{NAMES[a]} final cost")
            ax.set_ylabel(f"{NAMES[b]} final cost")
            ax.set_title(f"{NAMES[b]} lower on {better}, higher on {worse}, equal on {len(common) - better - worse}",
                         fontsize=8.5)
        suptitle(fig, "Per-program comparison (points below the diagonal favour the y-axis strategy)")
        caption(fig, "Every program is one point; no averaging hides regressions. Source: per_program.csv.")
        p.save(fig, "04_pairwise_scatter.png", ["per_program.csv"], "per-program cost scatter between strategies")

    # 5. search effort
    if rows:
        fig, ax = plt.subplots(figsize=(6, 3.2))
        data = [[max(1, r["candidates_evaluated"]) for g in groups for r in by[s][g]] for s in strategies]
        bp = ax.boxplot(data, patch_artist=True, widths=0.5, medianprops={"color": TEXT},
                        flierprops={"markersize": 3, "markeredgecolor": MUTED})
        for patch, s in zip(bp["boxes"], strategies):
            patch.set_facecolor(COLORS[s])
            patch.set_edgecolor("white")
        ax.set_yscale("log")
        ax.set_xticks(range(1, 4), [NAMES[s] for s in strategies])
        ax.set_ylabel("candidates evaluated per program (log)")
        ax.set_title("Search effort", loc="left", fontweight="bold")
        caption(fig, "Each evaluated candidate is executed on the full validation suite. Source: per_program.csv.")
        p.save(fig, "05_search_effort.png", ["per_program.csv"], "candidate evaluations per strategy")

    # 6. sensitivity
    if sens:
        cfgs = list(dict.fromkeys(r["weights"] for r in sens))
        fig, axes = plt.subplots(1, 2, figsize=(8, 3.2))
        for ax, (m, title) in zip(axes, (("instructions", "TAC instructions"), ("cycles", "simulated cycles"))):
            width = 0.38
            for j, s in enumerate(("fixed", "greedy")):
                vals = []
                for c in cfgs:
                    rs = [r for r in sens if r["weights"] == c and r["strategy"] == s and r[f"{m}_before"]]
                    vals.append(statistics.fmean((r[f"{m}_after"] - r[f"{m}_before"]) / r[f"{m}_before"] * 100 for r in rs))
                ax.bar([i + (j - 0.5) * width for i in range(len(cfgs))], vals, width - 0.03, color=COLORS[s], label=NAMES[s])
            ax.set_xticks(range(len(cfgs)), cfgs, rotation=25, fontsize=7.5)
            ax.set_title(f"mean change in {title} (%)", fontsize=9)
            ax.axhline(0, color=MUTED, linewidth=0.8)
        axes[0].legend(loc="lower left")
        fig.suptitle("Cost-weight sensitivity")
        caption(fig, y=-0.12, text="Each configuration re-runs the search with different weights; raw metric changes are comparable "
                     "across configurations, weighted costs are not. Source: sensitivity.csv.")
        p.save(fig, "06_weight_sensitivity.png", ["sensitivity.csv"], "metric changes under each weight config")

    # 7. measured speed-up distribution
    if timing:
        t = defaultdict(dict)
        for r in timing:
            t[(r["program_id"], r["backend"])][r["strategy"]] = r
        fig, axes = plt.subplots(1, 2, figsize=(8, 3.3), sharey=True)
        for ax, backend in zip(axes, ("interp", "compiled")):
            data, labels = [], []
            for s in ("fixed", "greedy", "beam", "greedy_noM8"):
                xs = [d["unoptimized"]["median_ns"] / d[s]["median_ns"] for (pid, be), d in t.items()
                      if be == backend and s in d and "unoptimized" in d and d[s]["median_ns"]]
                data.append(xs)
                labels.append(NAMES[s])
            bp = ax.boxplot(data, patch_artist=True, widths=0.5, medianprops={"color": TEXT},
                            flierprops={"markersize": 3, "markeredgecolor": MUTED})
            for patch, s in zip(bp["boxes"], ("fixed", "greedy", "beam", "greedy_noM8")):
                patch.set_facecolor(COLORS[s])
                patch.set_edgecolor("white")
            ax.axhline(1.0, color=MUTED, linewidth=0.8)
            ax.set_xticks(range(1, 5), labels, rotation=20)
            ax.set_title("Python TAC interpreter" if backend == "interp" else "TAC compiled to Python", fontsize=9)
        axes[0].set_ylabel("measured speed-up vs unoptimized (×)")
        suptitle(fig, "Measured wall-clock speed-up per program")
        caption(fig, y=-0.1, text="Median of interleaved repeats per program; >1 = faster than the naive TAC. Python execution "
                     "includes interpreter overhead; this is not native code. Source: timing.csv (raw: timing_raw.csv).")
        p.save(fig, "07_measured_speedup.png", ["timing.csv", "timing_raw.csv"], "measured speed-up distributions")

        # 8. simulated cycles vs measured time
        metrics = {(r["program_id"], r["strategy"]): r for r in rows}
        fig, axes = plt.subplots(1, 2, figsize=(8, 3.3))
        for ax, backend in zip(axes, ("interp", "compiled")):
            for s in ("unoptimized", "fixed", "greedy", "beam"):
                pts = [(metrics[(pid, s)]["cycles_after"], d[s]["median_ns"]) for (pid, be), d in t.items()
                       if be == backend and s in d and (pid, s) in metrics]
                if pts:
                    ax.scatter([a for a, _ in pts], [b / 1000 for _, b in pts], s=8, alpha=0.6, color=COLORS[s],
                               label=NAMES[s], edgecolors="none")
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_xlabel("simulated cycles (model)")
            ax.set_ylabel("measured median time (µs)")
            rho = summary["timing"][backend]["spearman_cycles_vs_time"] if summary and summary.get("timing") else None
            ax.set_title(f"{'interpreter' if backend == 'interp' else 'compiled'}: Spearman ρ = {rho}", fontsize=9)
        axes[0].legend(loc="upper left", fontsize=7)
        suptitle(fig, "Do simulated cycles predict measured time?")
        caption(fig, "One point per (program, strategy). Source: per_program.csv + timing.csv.")
        p.save(fig, "08_cycles_vs_time.png", ["per_program.csv", "timing.csv"], "simulated cycles vs measured time")

        # 9. reordering investigation
        fig, ax = plt.subplots(figsize=(5.4, 3.6))
        xs, ys = [], []
        for (pid, be), d in t.items():
            if be != "interp" or "greedy" not in d or "greedy_noM8" not in d:
                continue
            g, n = metrics.get((pid, "greedy")), metrics.get((pid, "greedy_noM8"))
            if g and n and n["cycles_after"]:
                xs.append((g["cycles_after"] - n["cycles_after"]) / n["cycles_after"] * 100)
                ys.append((d["greedy"]["median_ns"] - d["greedy_noM8"]["median_ns"]) / d["greedy_noM8"]["median_ns"] * 100)
        ax.scatter(xs, ys, s=12, color=COLORS["greedy"], alpha=0.7, edgecolors="white", linewidths=0.4)
        ax.axhline(0, color=MUTED, linewidth=0.8)
        ax.axvline(0, color=MUTED, linewidth=0.8)
        ax.set_xlabel("Δ simulated cycles, greedy vs greedy without M8 (%)")
        ax.set_ylabel("Δ measured time, interpreter (%)")
        ax.set_title("Instruction reordering (M8): simulator vs measurement", loc="left", fontweight="bold", fontsize=10)
        caption(fig, "Left of 0: M8 lowered simulated cycles. Below 0: measured faster. Points scattered around the "
                     "horizontal axis mean the stall model has no measurable counterpart in Python execution. "
                     "Source: per_program.csv + timing.csv.")
        p.save(fig, "09_reordering_effect.png", ["per_program.csv", "timing.csv"], "M8 effect: simulated vs measured")

    # 10. validator self-evaluation
    if veval:
        for proto in ("v2",):
            ve = veval[proto]
            kinds = list(ve["by_kind"])
            fig, ax = plt.subplots(figsize=(7.5, 3.2))
            bottom = [0] * len(kinds)
            for outcome, label in (("rejected", "rejected (detected)"), ("accepted_equivalent", "accepted, equivalent on held-out"),
                                   ("accepted_but_different", "accepted but different (missed)")):
                vals = [ve["by_kind"][k].get(outcome, 0) for k in kinds]
                ax.bar(range(len(kinds)), vals, 0.6, bottom=bottom, color=STATUS[outcome], label=label,
                       edgecolor="white", linewidth=1.5)
                bottom = [b + v for b, v in zip(bottom, vals)]
            for i, k in enumerate(kinds):
                miss = ve["by_kind"][k].get("accepted_but_different", 0)
                ax.text(i, bottom[i] + max(bottom) * 0.015, f"{miss} missed", ha="center", va="bottom", fontsize=7,
                        color="#b42727" if miss else MUTED)
            ax.set_xticks(range(len(kinds)), kinds, rotation=20)
            ax.set_ylabel("mutants")
            ax.legend(fontsize=7.5, loc="upper left")
            r1 = veval["review1"]
            ax.set_title(f"Validator self-test: detection {ve['detection_rate_on_nonequivalent']:.2%} "
                         f"(Review-1 vectors: {r1['detection_rate_on_nonequivalent']:.2%})", loc="left",
                         fontweight="bold", fontsize=10)
            caption(fig, y=-0.12, text=f"{ve['mutants']} injected-fault mutants on {ve['programs']} programs; ground truth = 300 "
                         "held-out inputs (itself finite). Source: validator_eval.json.")
            p.save(fig, "10_validator_selftest.png", ["validator_eval.json"], "validator fault-injection outcomes")

    # 11. dataset composition
    if dstats:
        fig, axes = plt.subplots(1, 2, figsize=(8.4, 3.2))
        sub = defaultdict(int)
        for c in dstats["by_category"]:
            sub[f"{c['group']} / {c['subgroup']}"] += c["accepted"]
        ks = list(sub)
        axes[0].barh(range(len(ks)), [sub[k] for k in ks], color="#2a78d6", height=0.55)
        for i, k in enumerate(ks):
            axes[0].text(sub[k] + 3, i, str(sub[k]), va="center", fontsize=8, color=TEXT)
        axes[0].set_yticks(range(len(ks)), ks)
        axes[0].invert_yaxis()
        axes[0].set_title(f"accepted programs (total {dstats['totals']['accepted']})", fontsize=9)
        reasons = defaultdict(int)
        for g, d in dstats["rejection_reasons"].items():
            for k, v in d.items():
                reasons[k if g != "external" or k.startswith("duplicate") else "external: " + k] += v
        top = sorted(reasons.items(), key=lambda kv: -kv[1])[:10]
        axes[1].barh(range(len(top)), [v for _, v in top], color="#b4b2a7", height=0.55)
        for i, (_, v) in enumerate(top):
            axes[1].text(v + 2, i, str(v), va="center", fontsize=8, color=TEXT)
        axes[1].set_yticks(range(len(top)), [k for k, _ in top], fontsize=7.5)
        axes[1].invert_yaxis()
        axes[1].set_title("top reasons for not accepting a record", fontsize=9)
        suptitle(fig, "Dataset miniarith-v1")
        caption(fig, "Source: dataset/miniarith-v1/statistics.json.")
        p.save(fig, "11_dataset_composition.png", ["../../dataset/miniarith-v1/statistics.json"], "dataset composition")

    with open(os.path.join(p.out, "index.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(p.index, fh, indent=2)
        fh.write("\n")
    return p.index


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    res_dir = argv[0] if argv else os.path.join(ROOT, "results", "final")
    index = plot_all(res_dir)
    for name in index:
        print(f"  plot: {os.path.join(res_dir, 'plots', name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
