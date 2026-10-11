"""Wall-clock timing of TAC programs.

Two backends are timed:
  interp    the TAC interpreter (execution/interpreter.py) - dominated by Python
            dispatch overhead per instruction, so it mostly tracks instruction count
  compiled  the TAC compiled to a Python function (execution/pycodegen.py) -
            removes dispatch overhead; still Python, so `*` vs `+` costs differ far
            less than in the simulated cycle model

Protocol (per program set, e.g. the unoptimised/fixed/greedy/beam versions of
one benchmark):
  * the same input batch is used for every variant,
  * `warmup` untimed runs per variant, then `repeats` timed rounds,
  * inside each round the variants are timed in an interleaved, rotating order
    to spread drift (CPU frequency, background load) evenly across variants,
  * the garbage collector is disabled while timing,
  * each sample = total ns to run the whole batch once; raw samples are returned
    so callers can store them.

Timing is the ONLY nondeterministic part of the experiments. Simulated cycles
are not CPU cycles; neither backend measures native code.
"""

import gc
import statistics
import time

from .interpreter import run
from .pycodegen import compile_tac


def _interp_runner(program, batch):
    def go():
        for inputs in batch:
            run(program, inputs)
    return go


def _compiled_runner(program, batch):
    fn = compile_tac(program).raw

    def go():
        for inputs in batch:
            try:
                fn(inputs, [])
            except Exception:  # runtime error ends that execution, like the interpreter
                pass
    return go


RUNNERS = {"interp": _interp_runner, "compiled": _compiled_runner}


def time_variants(variants, batch, backend="interp", warmup=3, repeats=15):
    """variants: dict name -> program. Returns dict name -> list of ns samples."""
    names = list(variants)
    runners = {n: RUNNERS[backend](variants[n], batch) for n in names}
    samples = {n: [] for n in names}
    for n in names:
        for _ in range(warmup):
            runners[n]()
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        for r in range(repeats):
            order = names[r % len(names):] + names[:r % len(names)]
            for n in order:
                t0 = time.perf_counter_ns()
                runners[n]()
                samples[n].append(time.perf_counter_ns() - t0)
    finally:
        if was_enabled:
            gc.enable()
    return samples


def summarize(samples, per=1):
    """Median / IQR / min of ns samples, divided by `per` (e.g. batch size)."""
    xs = sorted(s / per for s in samples)
    q = statistics.quantiles(xs, n=4) if len(xs) >= 2 else [xs[0]] * 3
    return {"median_ns": statistics.median(xs), "q1_ns": q[0], "q3_ns": q[2],
            "iqr_ns": q[2] - q[0], "min_ns": xs[0], "n": len(xs)}
