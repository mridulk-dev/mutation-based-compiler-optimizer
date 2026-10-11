"""Random-program fuzzer for the whole pipeline (correctness workload).

    python -m tests.fuzz [N] [SEED] [--cf] [--strategies greedy,fixed,beam] [--save-dir DIR]

For each random MiniArith program: compile, optimise, then check
  * the optimised TAC matches the independent AST interpreter on fresh inputs
    (catches anything the 25-case validator let through), and
  * no SAFE operator proposed a candidate the validator had to reject
    (catches operators that are unsound and only "work" thanks to validation).
`--cf` generates programs with if/else (control-flow extension).
Every failing program is written to --save-dir (default tests/regressions/found/)
together with its seed, so it can be replayed and turned into a regression test.
This workload is kept separate from the optimisation benchmark dataset.
"""

import argparse
import json
import os
import random
import sys

from execution.interpreter import run
from execution.reference import ast_run
from frontend.parser import parse
from frontend.tac import compile_source
from optimizer.search import optimize
from optimizer.strategies import STRATEGIES
from verify_pipeline import fresh_inputs

HERE = os.path.dirname(os.path.abspath(__file__))
FOUND_DIR = os.path.join(HERE, "regressions", "found")
CONSTS = [0, 0, 1, 1, 2, 2, 3, 4, 8, 5, 7, 16]
RELOPS = ["<", "<=", ">", ">=", "==", "!="]


def random_expr(rng, names, depth):
    if depth == 0 or rng.random() < 0.3:
        return str(rng.choice(CONSTS)) if rng.random() < 0.4 else rng.choice(names)
    if rng.random() < 0.1:
        return f"-({random_expr(rng, names, depth - 1)})"
    op = rng.choice("++--***/%")
    return f"({random_expr(rng, names, depth - 1)} {op} {random_expr(rng, names, depth - 1)})"


def random_program(rng):
    inputs = [f"i{k}" for k in range(rng.randint(1, 3))]
    names = list(inputs)
    lines = [f"input {', '.join(inputs)};"]
    for _ in range(rng.randint(2, 9)):
        if rng.random() < 0.3:
            lines.append(f"print {random_expr(rng, names, 3)};")
        else:
            # reuse existing names often, to exercise redefinition / kill logic
            target = rng.choice(names) if rng.random() < 0.4 else rng.choice("abcdxyz")
            lines.append(f"{target} = {random_expr(rng, names, 3)};")
            if target not in names:
                names.append(target)
    lines.append(f"print {random_expr(rng, names, 2)};")
    return "\n".join(lines)


def _cf_block(rng, names, depth, n):
    """Statements for one block; only variables defined on every path are used."""
    lines, local = [], list(names)
    for _ in range(n):
        r = rng.random()
        if r < 0.2 and depth > 0:
            cond = random_expr(rng, local, 1)
            if rng.random() < 0.7:
                cond = f"{cond} {rng.choice(RELOPS)} {random_expr(rng, local, 1)}"
            then, then_defs = _cf_block(rng, local, depth - 1, rng.randint(1, 3))
            if rng.random() < 0.7:
                other, other_defs = _cf_block(rng, local, depth - 1, rng.randint(1, 3))
                lines.append(f"if ({cond}) {{ {' '.join(then)} }} else {{ {' '.join(other)} }}")
                local += [v for v in then_defs if v in other_defs and v not in local]
            else:
                lines.append(f"if ({cond}) {{ {' '.join(then)} }}")
        elif r < 0.45:
            lines.append(f"print {random_expr(rng, local, 2)};")
        else:
            target = rng.choice(local) if rng.random() < 0.4 else rng.choice("abcxyz")
            lines.append(f"{target} = {random_expr(rng, local, 2)};")
            if target not in local:
                local.append(target)
    return lines, local


def random_cf_program(rng):
    inputs = [f"i{k}" for k in range(rng.randint(1, 3))]
    body, names = _cf_block(rng, inputs, 2, rng.randint(3, 8))
    return "\n".join([f"input {', '.join(inputs)};", *body, f"print {random_expr(rng, names, 2)};"])


def save_failure(save_dir, kind, seed, k, source, detail):
    os.makedirs(save_dir, exist_ok=True)
    base = os.path.join(save_dir, f"{kind}_seed{seed}_{k}")
    with open(base + ".src", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(source + "\n")
    with open(base + ".json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"kind": kind, "seed": seed, "index": k, "detail": detail}, fh, indent=2, default=str)


def fuzz(n=200, seed=1, verbose=True, cf=False, strategies=("greedy",), save_dir=None):
    rng = random.Random(seed)
    unsound, wrong = [], []
    total_steps = 0
    gen = random_cf_program if cf else random_program
    for k in range(n):
        source = gen(rng)
        program = compile_source(source)
        ast = parse(source)
        n_in = sum(1 for i in program if i.op == "input")
        cases = fresh_inputs(n_in, n=150, seed=k)
        for strat in strategies:
            if strat == "greedy":
                res = optimize(program, n_cases=25, seed=k)
                optimized, rejected, steps = res.optimized, res.rejected_total, len(res.steps)
                rej_detail = dict(res.rejected_by_op)
            else:
                res = STRATEGIES[strat](program, n_cases=25, seed=k)
                optimized, rejected, steps = res.optimized, res.candidates_rejected, len(res.steps)
                rej_detail = {"rejected": rejected}
            total_steps += steps
            if rejected:
                unsound.append((source, rej_detail))
                if save_dir:
                    save_failure(save_dir, "unsound", seed, k, source, {"strategy": strat, "rejected": rej_detail})
            for case in cases:
                r = run(optimized, case)
                if ast_run(ast, case) != (r.outputs, r.error is not None):
                    wrong.append((source, case))
                    if save_dir:
                        save_failure(save_dir, "wrong", seed, k, source, {"strategy": strat, "input": case})
                    break
    if verbose:
        print(f"{n} random {'control-flow ' if cf else ''}programs x {len(strategies)} strategies, "
              f"{total_steps} accepted mutations")
        print(f"  programs where a safe operator proposed an invalid candidate: {len(unsound)}")
        print(f"  programs whose optimised output disagrees with the oracle:   {len(wrong)}")
        for source, ops in unsound[:3]:
            print(f"\n  unsound proposal {ops} in:\n    " + source.replace("\n", "\n    "))
        for source, case in wrong[:3]:
            print(f"\n  WRONG on input {case}:\n    " + source.replace("\n", "\n    "))
    return unsound, wrong


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("n", nargs="?", type=int, default=200)
    ap.add_argument("seed", nargs="?", type=int, default=1)
    ap.add_argument("--cf", action="store_true", help="generate if/else programs")
    ap.add_argument("--strategies", default="greedy")
    ap.add_argument("--save-dir", default=FOUND_DIR)
    args = ap.parse_args(argv)
    unsound, wrong = fuzz(args.n, args.seed, cf=args.cf, strategies=tuple(args.strategies.split(",")),
                          save_dir=args.save_dir)
    return 1 if unsound or wrong else 0


if __name__ == "__main__":
    sys.exit(main())
