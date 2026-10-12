"""Optimisation strategies compared in the experiments.

All strategies share
  * the same mutation operators (optimizer/mutations.py),
  * the same correctness check (execution/validator.py, differential testing
    against the ORIGINAL program on the same 25 seeded input vectors), and
  * the same cost function (optimizer/cost.py),
so a comparison measures the *search strategy*, not different transformations.

    unoptimized   the naive TAC from the compiler, unchanged
    fixed         a classical fixed-order pass pipeline (FIXED_ORDER), each pass
                  applied to a fixpoint and the whole sequence repeated until
                  nothing changes, then a cycle-driven scheduling pass (M8)
    greedy        the Review-1 greedy best-improvement search (optimizer/search.py)
    beam          beam search: keep the `width` cheapest distinct valid programs
                  per depth, stop after `patience` depths without a new best

Every program a strategy accepts has passed the validator; candidates that fail
are counted as rejected and never applied.
"""

import time
from dataclasses import dataclass, field

from execution.validator import Validator

from .cost import cost, measure
from .mutations import OPERATORS, generate_mutations
from .search import optimize as greedy_optimize

EPSILON = 1e-12

# Documented pass order for the fixed pipeline: simplify constants first so the
# later passes see folded values, then copies/identities, then redundancy (CSE),
# then strength reduction, dead-code removal and branch folding.
FIXED_ORDER = ("M3", "M7", "M9", "M1", "M2", "M6", "M9", "M4", "M5", "M10")
OPS_BY_ID = dict(OPERATORS)


@dataclass(frozen=True)
class Evaluation:
    valid: bool
    metrics: object
    cost: object


class Evaluator:
    """Memoised validation + costing of candidate programs for one original."""

    def __init__(self, original, n_cases=25, seed=42, weights=None, structured=False, protocol=None):
        self.validator = Validator(original, n_cases=n_cases, seed=seed, structured=structured, protocol=protocol)
        self.base = measure(original, self.validator.reference)
        self.weights = weights
        self.cache = {}
        self.executed = 0  # distinct programs actually executed on the test suite

    def evaluate(self, program):
        hit = self.cache.get(program)
        if hit is not None:
            return hit
        self.executed += 1
        valid, runs = self.validator.check(program)
        if valid:
            m = measure(program, runs)
            result = Evaluation(True, m, cost(m, self.base, self.weights))
        else:
            result = Evaluation(False, None, None)
        self.cache[program] = result
        return result


@dataclass
class StrategyResult:
    strategy: str
    original: tuple
    optimized: tuple
    base_metrics: object
    final_metrics: object
    final_cost: float
    steps: list = field(default_factory=list)  # dicts: op_id, description, cost
    candidates_evaluated: int = 0
    candidates_rejected: int = 0
    programs_executed: int = 0
    seconds: float = 0.0
    status: str = "ok"  # ok | timeout | budget

    @property
    def candidates_valid(self):
        return self.candidates_evaluated - self.candidates_rejected

    @property
    def applied_by_op(self):
        counts = {}
        for s in self.steps:
            counts[s["op_id"]] = counts.get(s["op_id"], 0) + 1
        return counts


def run_unoptimized(program, n_cases=25, seed=42, weights=None, structured=False, protocol=None, **_):
    start = time.perf_counter()
    ev = Evaluator(program, n_cases, seed, weights, structured, protocol)
    return StrategyResult("unoptimized", program, program, ev.base, ev.base, 1.0,
                          programs_executed=1, seconds=time.perf_counter() - start)


def run_greedy(program, n_cases=25, seed=42, weights=None, operators=OPERATORS, time_limit=None,
               structured=False, protocol=None, **_):
    res = greedy_optimize(program, operators, n_cases=n_cases, seed=seed, weights=weights, structured=structured,
                          protocol=protocol)
    steps = [dict(op_id=s.op_id, description=s.description, cost=s.cost) for s in res.steps]
    return StrategyResult("greedy", program, res.optimized, res.base_metrics, res.final_metrics, res.final_cost,
                          steps, res.candidates_total, res.rejected_total, res.candidates_total + 1, res.seconds)


def run_fixed(program, n_cases=25, seed=42, weights=None, order=FIXED_ORDER, max_rounds=50,
              max_steps=5000, time_limit=None, structured=False, protocol=None, **_):
    """Apply each pass in `order` to a fixpoint (first valid candidate each time,
    regardless of cost - like a classical pass), repeat the sequence until a full
    round changes nothing, then schedule with M8 swaps that lower simulated cycles."""
    start = time.perf_counter()
    ev = Evaluator(program, n_cases, seed, weights, structured, protocol)
    current = program
    res = StrategyResult("fixed", program, program, ev.base, ev.base, 1.0)

    def apply_first(op_ids, accept):
        nonlocal current
        for m in generate_mutations(current, [(op, OPS_BY_ID[op]) for op in op_ids]):
            if m.program == current:
                continue
            res.candidates_evaluated += 1
            e = ev.evaluate(m.program)
            if not e.valid:
                res.candidates_rejected += 1
                continue
            if not accept(e):
                continue
            current = m.program
            res.steps.append(dict(op_id=m.op_id, description=m.description, cost=e.cost))
            return True
        return False

    def timed_out():
        return time_limit is not None and time.perf_counter() - start > time_limit

    for _ in range(max_rounds):
        changed = False
        for op in order:
            while len(res.steps) < max_steps and apply_first([op], lambda e: True):
                changed = True
                if timed_out():
                    break
        if not changed or timed_out() or len(res.steps) >= max_steps:
            break
    # scheduling pass: list-scheduling style, accept swaps that lower cycles
    while len(res.steps) < max_steps and not timed_out():
        cur_cycles = ev.evaluate(current).metrics.cycles
        if not apply_first(["M8"], lambda e: e.metrics.cycles < cur_cycles - EPSILON):
            break

    final = ev.evaluate(current)
    res.optimized, res.final_metrics, res.final_cost = current, final.metrics, final.cost
    res.programs_executed = ev.executed
    res.seconds = time.perf_counter() - start
    if timed_out():
        res.status = "timeout"
    elif len(res.steps) >= max_steps:
        res.status = "budget"
    return res


@dataclass
class _Node:
    program: tuple
    cost: float
    metrics: object
    parent: object = None
    op_id: str = None
    description: str = None


def run_beam(program, n_cases=25, seed=42, weights=None, width=4, patience=3, max_depth=300,
             max_evaluations=50000, operators=OPERATORS, time_limit=None, structured=False, protocol=None, **_):
    start = time.perf_counter()
    ev = Evaluator(program, n_cases, seed, weights, structured, protocol)
    root = _Node(program, 1.0, ev.base)
    beam, best = [root], root
    visited = {program}
    res = StrategyResult("beam", program, program, ev.base, ev.base, 1.0)
    stale = 0
    for _depth in range(max_depth):
        pool = []
        seen = set()
        for node in beam:
            for m in generate_mutations(node.program, operators):
                if m.program in visited or m.program in seen:
                    continue
                seen.add(m.program)
                res.candidates_evaluated += 1
                e = ev.evaluate(m.program)
                if not e.valid:
                    res.candidates_rejected += 1
                    continue
                pool.append(_Node(m.program, e.cost, e.metrics, node, m.op_id, m.description))
        if not pool:
            break
        # stable sort: ties keep generation order, so runs are deterministic
        pool.sort(key=lambda n: n.cost)
        beam = pool[:width]
        visited.update(n.program for n in beam)
        if beam[0].cost < best.cost - EPSILON:
            best, stale = beam[0], 0
        else:
            stale += 1
            if stale >= patience:
                break
        if res.candidates_evaluated >= max_evaluations:
            res.status = "budget"
            break
        if time_limit is not None and time.perf_counter() - start > time_limit:
            res.status = "timeout"
            break
    path = []
    node = best
    while node.parent is not None:
        path.append(dict(op_id=node.op_id, description=node.description, cost=node.cost))
        node = node.parent
    res.steps = list(reversed(path))
    res.optimized, res.final_metrics, res.final_cost = best.program, best.metrics, best.cost
    res.programs_executed = ev.executed
    res.seconds = time.perf_counter() - start
    return res


STRATEGIES = {
    "unoptimized": run_unoptimized,
    "fixed": run_fixed,
    "greedy": run_greedy,
    "beam": run_beam,
}
