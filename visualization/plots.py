"""Static PNG plots of optimisation results (matplotlib, headless)."""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from optimizer.mutations import OPERATOR_NAMES  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
BASELINE_GRAY = "#b4b2a7"
TEXT = "#1f1f1e"
MUTED = "#6b6a63"
GRID = "#e4e3dc"


def _style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_convergence(results, path):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for i, (name, res) in enumerate(results.items()):
        history = res.cost_history
        color = SERIES[i % len(SERIES)]
        ax.plot(range(len(history)), history, color=color, linewidth=2, marker="o", markersize=4, label=name)
        ax.annotate(f"{history[-1]:.3f}", (len(history) - 1, history[-1]), xytext=(5, 0),
                    textcoords="offset points", va="center", fontsize=8, color=TEXT)
    _style(ax)
    ax.set_xlabel("Accepted mutation (iteration)", color=TEXT)
    ax.set_ylabel("Normalised cost (original = 1.0)", color=TEXT)
    ax.set_title("Greedy search convergence", color=TEXT, loc="left", fontsize=12)
    ax.set_ylim(0, 1.05)
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_metrics(results, path):
    metrics = [("instructions", "Instructions"), ("arith_ops", "Arithmetic ops"),
               ("temps", "Temporaries"), ("cycles", "Simulated cycles")]
    names = list(results)
    x = range(len(names))
    width = 0.38
    fig, axes = plt.subplots(2, 2, figsize=(10, 6.5))
    for ax, (key, label) in zip(axes.flat, metrics):
        before = [getattr(results[n].base_metrics, key) for n in names]
        after = [getattr(results[n].final_metrics, key) for n in names]
        ax.bar([i - width / 2 - 0.01 for i in x], before, width, color=BASELINE_GRAY, label="Original")
        ax.bar([i + width / 2 + 0.01 for i in x], after, width, color=SERIES[0], label="Optimised")
        _style(ax)
        ax.set_title(label, color=TEXT, loc="left", fontsize=10)
        ax.set_xticks(list(x))
        ax.set_xticklabels([n.split("_", 1)[-1] for n in names], fontsize=8)
    axes.flat[0].legend(frameon=False, fontsize=8)
    fig.suptitle("Original vs optimised TAC", color=TEXT, x=0.01, ha="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_operator_usage(results, path):
    totals = {}
    for res in results.values():
        for op_id, n in res.applied_by_op.items():
            totals[op_id] = totals.get(op_id, 0) + n
    items = sorted(totals.items(), key=lambda kv: kv[1])
    labels = [f"{op} {OPERATOR_NAMES[op]}" for op, _ in items]
    counts = [n for _, n in items]
    fig, ax = plt.subplots(figsize=(8, 0.45 * len(items) + 1.2))
    bars = ax.barh(labels, counts, color=SERIES[0], height=0.6)
    for bar, n in zip(bars, counts):
        ax.text(bar.get_width() + 0.2, bar.get_y() + bar.get_height() / 2, str(n),
                va="center", fontsize=8, color=TEXT)
    _style(ax)
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_xlabel("Times applied (all benchmarks)", color=TEXT)
    ax.set_title("Accepted mutations by operator", color=TEXT, loc="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def make_all(results, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "convergence": os.path.join(out_dir, "convergence.png"),
        "metrics": os.path.join(out_dir, "metrics_comparison.png"),
        "operators": os.path.join(out_dir, "operator_usage.png"),
    }
    plot_convergence(results, paths["convergence"])
    plot_metrics(results, paths["metrics"])
    plot_operator_usage(results, paths["operators"])
    return paths
