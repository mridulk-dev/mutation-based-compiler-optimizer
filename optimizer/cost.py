"""Weighted, normalised cost model.

    cost = 0.4 * instructions / instructions_0
         + 0.3 * arith_ops    / arith_ops_0
         + 0.2 * temps        / temps_0
         + 0.1 * cycles       / cycles_0

Each metric is normalised by the original program's value, so the original
program always has cost 1.0 and lower is better.

`WEIGHTS` is the Review-1 baseline configuration and stays the default so that
results remain comparable with earlier runs. `WEIGHT_CONFIGS` lists the
alternative configurations used in the sensitivity analysis.
Label pseudo-instructions (control-flow extension) are not counted as
instructions.
"""

from dataclasses import asdict, dataclass

from frontend.tac import ARITH_OPS, is_temp

WEIGHTS = {"instructions": 0.4, "arith_ops": 0.3, "temps": 0.2, "cycles": 0.1}

WEIGHT_CONFIGS = {
    "baseline": WEIGHTS,
    "uniform": {"instructions": 0.25, "arith_ops": 0.25, "temps": 0.25, "cycles": 0.25},
    "size": {"instructions": 0.7, "arith_ops": 0.1, "temps": 0.1, "cycles": 0.1},
    "cycles": {"instructions": 0.1, "arith_ops": 0.1, "temps": 0.1, "cycles": 0.7},
    "cycles_only": {"instructions": 0.0, "arith_ops": 0.0, "temps": 0.0, "cycles": 1.0},
    "no_temps": {"instructions": 0.5, "arith_ops": 0.35, "temps": 0.0, "cycles": 0.15},
}


@dataclass(frozen=True)
class Metrics:
    instructions: int
    arith_ops: int
    temps: int
    cycles: float  # mean simulated cycles over the test suite

    def as_dict(self):
        return asdict(self)


def measure(program, results):
    return Metrics(
        instructions=sum(1 for ins in program if ins.op != "label"),
        arith_ops=sum(1 for ins in program if ins.op in ARITH_OPS),
        temps=len({ins.dest for ins in program if is_temp(ins.dest)}),
        cycles=sum(r.cycles for r in results) / len(results),
    )


def cost(metrics, baseline, weights=None):
    total = 0.0
    for name, weight in (weights or WEIGHTS).items():
        base = getattr(baseline, name)
        total += weight * getattr(metrics, name) / (base if base > 0 else 1)
    return total
