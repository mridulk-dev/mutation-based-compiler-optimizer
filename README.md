# Automated Mutation-Based Compiler Code Optimization (Review-1 prototype)

Search-based optimiser for three-address code (TAC). Rewrite rules ("mutations") only
*propose* changes. A change is kept only when differential testing shows it still behaves
like the original program and it lowers a weighted cost.

```
source (.src) -> lexer/parser -> naive TAC -> [ mutate -> validate -> cost -> pick best ]* -> optimised TAC -> results + plots
```

Architecture and workflow diagrams, plus a 1–2 sentence description of every module, are in
[docs/project_overview.md](docs/project_overview.md).

## Run

```
pip install -r requirements.txt          # matplotlib, only for plots
python main.py                           # all benchmarks: trace, before/after TAC, metrics, plots
python main.py benchmarks/b5_polynomial.src
python main.py --unsafe --quiet          # adds a deliberately wrong operator; shows validator rejections
python -m unittest discover -s tests -t . -v
python verify_pipeline.py                # stage-by-stage check of every benchmark
python -m tests.fuzz 1000 77             # random-program fuzzing against an AST oracle
python -m benchmarks.generate_dataset    # regenerate the 15-program Review-1 dataset (seed 2026)
python run_dataset.py                    # optimise all 15 -> results/results.csv, candidate_log.csv, summary.json
```

## Review-1 dataset

`benchmarks/generate_dataset.py` builds 15 programs with seeded template generators: arithmetic (2),
constant-heavy (2), dead-code (3), common-subexpression (3), strength-reduction (2), mixed (3). Each
entry in `benchmarks/dataset/review1_dataset.json` has an id, a category, the source, 3-5 test
inputs, and the expected outputs. Those outputs come from running the source on the reference AST
interpreter (`execution/reference.py`), which shares no code with the TAC generator or optimiser.
The same seed always produces a byte-identical file, and `run_dataset.py` checks this before running.

`run_dataset.py` writes:
- `results/results.csv`: one row per program, with before/after metrics, final cost, iterations,
  candidates evaluated/valid/rejected, operators applied, dataset tests passed, and unseen inputs
  that match the reference.
- `results/candidate_log.csv`: one row per candidate the search evaluated, with its operator,
  description, validity, metrics, cost, and whether it was accepted.
- `results/summary.json`: the dataset hash, config, environment, correctness totals, and
  per-category and per-operator statistics.

Two runs give identical files, except for the `runtime_seconds` and `*_seconds` timing fields.

Outputs go to `results/`: `summary.csv`, `<benchmark>/{original.tac, optimized.tac, trace.json}`,
and `plots/{convergence, metrics_comparison, operator_usage}.png`.

## Source language (MiniArith)

Straight-line, integer-only: `input a, b;`, `x = expr;`, `print expr;`. It supports `+ - * / %`,
unary minus, parentheses and `#` comments. `/` and `%` follow C rules (they truncate toward zero).
Names like `t1` are reserved for compiler temporaries.

## Layout

| Directory | Contents |
|---|---|
| `frontend/` | `lexer.py`, `parser.py` (recursive descent -> AST), `tac.py` (TAC IR, naive generator, TAC text parser) |
| `execution/` | `interpreter.py` (TAC interpreter + cycle model), `validator.py` (differential testing), `reference.py` (AST interpreter used as the oracle) |
| `optimizer/` | `mutations.py` (operators), `cost.py`, `search.py` (greedy best-improvement) |
| `visualization/` | `plots.py` |
| `benchmarks/` | seven `.src` programs: b1-b5 each target particular operators; b6 covers variable redefinition, b7 covers negative division and division by zero |
| `tests/` | unit tests (frontend, each operator, validator, cost, search) and `fuzz.py` (random-program fuzzer) |
| `verify_pipeline.py` | per benchmark: TAC checked against an AST oracle, each operator run alone, the validator mutation-tested, cost recomputed by hand, and full search checked on 300 fresh inputs |

## Mutation operators

| ID | Operator | Rewrite |
|---|---|---|
| M1 | Zero-Add Removal | `a+0`, `0+a`, `a-0` -> `a` |
| M2 | One-Mul Removal | `a*1`, `1*a`, `a/1` -> `a` |
| M3 | Constant Folding | `c1 op c2` -> `c` (not folded when the result would be an error, e.g. `/0`) |
| M4 | Strength Reduction | `a*2` -> `a+a`; `a*2^k` -> `a<<k` |
| M5 | Dead Assignment Removal | removes an assignment whose value is never read (backward liveness); never removes `input` |
| M6 | Common Subexpression Elimination | `x = a op b` with an earlier identical, still-valid expression -> `x = t` (treats `+` and `*` as commutative) |
| M7 | Constant Propagation | `k = c` -> replaces later uses of `k` with `c` and removes the copy if it is now dead |
| M8 | Instruction Reordering | swaps adjacent independent instructions (no RAW/WAR/WAW dependency, I/O order kept) |
| M9 | Copy Propagation *(extra)* | `x = y` -> replaces later uses of `x` with `y` and removes the copy if it is now dead |
| MX | Unchecked Reordering *(unsafe, `--unsafe` only)* | swaps *dependent* pairs, to show the validator at work |

Why M7/M9 remove the copy themselves: propagation on its own leaves the cost unchanged, so a
strictly-improving greedy search would never take it. Combining "propagate + delete dead copy" into
one mutation took b5 from cost 0.82 to 0.45.

## Cost model

`cost = 0.4·I/I0 + 0.3·A/A0 + 0.2·T/T0 + 0.1·C/C0` (the original program has cost 1.0).
I = instructions, A = arithmetic ops, T = distinct temporaries, C = mean **simulated cycles**
over the test suite. Execution time comes from a deterministic cycle model, not wall-clock time,
so results are reproducible: `+ - << neg copy io` = 1, `*` = 3, `/ %` = 10, plus a 1-cycle stall
when an instruction reads the result of the instruction just before it. The stall term is what
gives M8 something to improve.

## Correctness validation

The original program and each candidate both run on 25 deterministic input vectors (edge values
0, ±1, ±2, 7, 1000, -999, then seeded random). Their printed output **and** error status (e.g.
division by zero) must match exactly. This is testing, not proof. The test suite also re-checks
each optimised benchmark against 40 fresh inputs the search never saw.

### Runtime errors are observable

Division or modulo by a non-constant divisor may fail with a division-by-zero error, and that
error is part of the program's behaviour. M5 therefore never deletes such an instruction, and M8
never moves one across `input`/`print`. Fuzzing found this: before the rule, 19 of 300 random
programs were optimised incorrectly, because the 25 validation inputs often missed the exact
input that divides by zero. After the fix, 1,300 fuzzed programs (about 20k accepted mutations)
showed 0 wrong results and 0 invalid proposals. The lesson is that operators have to be sound on
their own; test-based validation is a safety net, not a guarantee.

## Search

Greedy best-improvement. Each iteration generates every single-site mutation of the current
program, removes duplicates, rejects the ones that fail validation, scores the rest, and moves
to the cheapest one if it strictly lowers the cost. The search stops at a local optimum.

## Current limitations (planned for later reviews)

- Straight-line code only: no branches or loops, so no CFG or data-flow across blocks.
- Unbounded Python integers; 32-bit overflow is not modelled.
- Greedy search can get stuck in local optima; simulated annealing or beam search would be the
  natural next step.
- Validation is test-based and does not prove equivalence.
