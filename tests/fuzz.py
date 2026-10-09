"""Random-program fuzzer for the whole pipeline.

    python -m tests.fuzz [N] [SEED]

For each random MiniArith program: compile, optimise, then check
  * the optimised TAC matches an independent AST interpreter on fresh inputs
    (catches anything the 25-case validator let through), and
  * no SAFE operator proposed a candidate the validator had to reject
    (catches operators that are unsound and only "work" thanks to validation).
"""

import random
import sys

from execution.interpreter import run
from execution.reference import ast_run
from frontend.parser import parse
from frontend.tac import compile_source
from optimizer.search import optimize
from verify_pipeline import fresh_inputs

CONSTS = [0, 0, 1, 1, 2, 2, 3, 4, 8, 5, 7, 16]


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


def fuzz(n=200, seed=1, verbose=True):
    rng = random.Random(seed)
    unsound, wrong = [], []
    total_steps = 0
    for k in range(n):
        source = random_program(rng)
        program = compile_source(source)
        res = optimize(program, n_cases=25, seed=k)
        total_steps += len(res.steps)
        if res.rejected_total:
            unsound.append((source, dict(res.rejected_by_op)))
        ast = parse(source)
        n_in = sum(1 for i in program if i.op == "input")
        for case in fresh_inputs(n_in, n=150, seed=k):
            r = run(res.optimized, case)
            if ast_run(ast, case) != (r.outputs, r.error is not None):
                wrong.append((source, case))
                break
    if verbose:
        print(f"{n} random programs, {total_steps} accepted mutations")
        print(f"  programs where a safe operator proposed an invalid candidate: {len(unsound)}")
        print(f"  programs whose optimised output disagrees with the oracle:   {len(wrong)}")
        for source, ops in unsound[:3]:
            print(f"\n  unsound proposal {ops} in:\n    " + source.replace("\n", "\n    "))
        for source, case in wrong[:3]:
            print(f"\n  WRONG on input {case}:\n    " + source.replace("\n", "\n    "))
    return unsound, wrong


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    unsound, wrong = fuzz(n, seed)
    sys.exit(1 if unsound or wrong else 0)
