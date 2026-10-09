"""Weighted, normalised cost model.

    cost = 0.4 * instructions / instructions_0
         + 0.3 * arith_ops    / arith_ops_0
         + 0.2 * temps        / temps_0
         + 0.1 * cycles       / cycles_0

Each metric is normalised by the original program's value, so the original
program always has cost 1.0 and lower is better.
"""

from dataclasses import asdict, dataclass

from frontend.tac import ARITH_OPS, is_temp

WEIGHTS = {"instructions": 0.4, "arith_ops": 0.3, "temps": 0.2, "cycles": 0.1}


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
        instructions=len(program),
        arith_ops=sum(1 for ins in program if ins.op in ARITH_OPS),
        temps=len({ins.dest for ins in program if is_temp(ins.dest)}),
        cycles=sum(r.cycles for r in results) / len(results),
    )


def cost(metrics, baseline):
    total = 0.0
    for name, weight in WEIGHTS.items():
        base = getattr(baseline, name)
        total += weight * getattr(metrics, name) / (base if base > 0 else 1)
    return total
