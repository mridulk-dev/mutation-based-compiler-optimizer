"""Greedy iterative best-improvement search.

Each iteration:
  1. generate every single-site mutation of the current program,
  2. execute each candidate on the test suite and discard any whose
     observable behaviour differs from the ORIGINAL program,
  3. compute the cost of the valid candidates,
  4. move to the cheapest one if it strictly improves the current cost.
Stops when no valid candidate improves the cost (a local optimum).
"""

import time
from collections import Counter
from dataclasses import dataclass, field

from execution.validator import Validator

from .cost import cost, measure
from .mutations import OPERATOR_NAMES, OPERATORS, generate_mutations

EPSILON = 1e-12


@dataclass
class Step:
    iteration: int
    op_id: str
    description: str
    cost: float
    metrics: dict
    candidates: int
    rejected: int
    candidate_index: int


@dataclass
class SearchResult:
    original: tuple
    optimized: tuple
    base_metrics: object
    final_metrics: object
    final_cost: float
    steps: list = field(default_factory=list)
    candidates_total: int = 0
    rejected_total: int = 0
    rejected_by_op: Counter = field(default_factory=Counter)
    seconds: float = 0.0

    @property
    def applied_by_op(self):
        return Counter(step.op_id for step in self.steps)

    @property
    def cost_history(self):
        return [1.0] + [step.cost for step in self.steps]


def optimize(program, operators=OPERATORS, max_iters=500, n_cases=25, seed=42, log=None, on_candidate=None):
    """on_candidate, if given, is called once per evaluated candidate with a dict
    (iteration, index, mutation, valid, metrics, cost, improves) for logging."""
    start = time.perf_counter()
    validator = Validator(program, n_cases=n_cases, seed=seed)
    base_metrics = measure(program, validator.reference)
    result = SearchResult(program, program, base_metrics, base_metrics, cost(base_metrics, base_metrics))
    current, current_cost = program, result.final_cost

    for iteration in range(1, max_iters + 1):
        seen = {current}
        best = None
        n_candidates = n_rejected = 0
        for mutation in generate_mutations(current, operators):
            if mutation.program in seen:
                continue
            seen.add(mutation.program)
            index = n_candidates
            n_candidates += 1
            valid, runs = validator.check(mutation.program)
            if not valid:
                n_rejected += 1
                result.rejected_by_op[mutation.op_id] += 1
                if on_candidate:
                    on_candidate(dict(iteration=iteration, index=index, mutation=mutation, valid=False,
                                      metrics=None, cost=None, improves=False))
                continue
            metrics = measure(mutation.program, runs)
            c = cost(metrics, base_metrics)
            improves = c < current_cost - EPSILON
            if on_candidate:
                on_candidate(dict(iteration=iteration, index=index, mutation=mutation, valid=True,
                                  metrics=metrics, cost=c, improves=improves))
            if improves and (best is None or c < best[0] - EPSILON):
                best = (c, mutation, metrics, index)

        result.candidates_total += n_candidates
        result.rejected_total += n_rejected
        if best is None:
            if log:
                log(f"  iter {iteration:3d}: {n_candidates} candidates, {n_rejected} rejected, "
                    f"no improvement -> converged")
            break

        current_cost, mutation, metrics, index = best
        current = mutation.program
        result.steps.append(Step(iteration, mutation.op_id, mutation.description, current_cost,
                                 metrics.as_dict(), n_candidates, n_rejected, index))
        result.final_metrics = metrics
        if log:
            log(f"  iter {iteration:3d}: cost {current_cost:.4f}  {mutation.op_id} "
                f"{OPERATOR_NAMES[mutation.op_id]:<32} {mutation.description}")

    result.optimized = current
    result.final_cost = current_cost
    result.seconds = time.perf_counter() - start
    return result
