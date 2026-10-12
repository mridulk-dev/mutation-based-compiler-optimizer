# Automated Mutation-Based Compiler Code Optimization

This project is a search-based optimiser for three-address code (TAC). A small compiler
turns **MiniArith** programs into deliberately naive TAC. Rewrite rules ("mutations")
*propose* changes, and a differential-testing validator rejects any candidate whose printed
output or runtime error differs from the original program. A search strategy then keeps the
cheapest valid program under a weighted cost model.

```
source → lexer/parser → naive TAC → [ mutate → validate → cost → select ]* → optimised TAC
```

This release takes the Review-1 prototype to a complete, reproducible project. It adds:

- a validated **454-program dataset** in three separately reported groups;
- **fair baselines** that share operators, validator and cost function: unoptimised, a
  fixed-order pass pipeline, greedy search and beam search;
- **measured wall-clock time** alongside the simulated cost;
- a **local web dashboard** wired to the real compiler and artifacts;
- a staged **if/else + CFG** extension;
- a one-command reproducibility pipeline and CI.

Results: [docs/results.md](docs/results.md) (generated from logs) · Report draft:
[docs/report.md](docs/report.md) · Demo guide: [docs/demo_guide.md](docs/demo_guide.md)

## Quick start

```bash
pip install -r requirements.txt              # matplotlib (plots only); everything else is stdlib
python -m dashboard.server                   # dashboard → http://127.0.0.1:8765
python main.py benchmarks/b5_polynomial.src  # CLI: one program, full trace (Review-1 driver)
python -m unittest discover -s tests -t .    # unit / property / regression / dataset / dashboard tests
python reproduce.py                          # full pipeline (≈ 47 min on a 12-thread laptop): dataset check, tests, fuzzing,
                                             #   experiments, plots, dashboard/data agreement
python reproduce.py --quick                  # CI-sized version (small fuzz, 30-program subset)
```

Requirements: Python ≥ 3.10 (developed with 3.13.3). gcc is optional; it is used only to
cross-check the C-derived dataset fragments.

On Windows, some file names in `jotai_unique_300/` exceed the default path-length limit when the
repository sits in a deep folder. Clone with `git clone -c core.longpaths=true <url>`, or clone
into a short path.

## Commands

| Task | Command |
|---|---|
| Build the dataset / verify it reproduces | `python -m benchmarks.build` / `python -m benchmarks.build --check` |
| Re-run deduplication on a manifest | `python -m benchmarks.dedup dataset/miniarith-v1/manifest.jsonl` |
| Full experiments (all strategies, sensitivity, timing) | `python -m experiments.run_experiments` → `results/final/` |
| Subset run | `python -m experiments.run_experiments --limit 40 --out results/smoke` |
| Validator fault-injection study | `python -m experiments.validator_eval` |
| Plots and results doc from saved logs | `python -m experiments.plots` then `python -m experiments.report` |
| Fuzzing (separate correctness workload) | `python -m tests.fuzz 1300 77` · `python -m tests.fuzz 300 11 --cf --strategies greedy,fixed,beam` |
| Review-1 artifacts (unchanged) | `python -m benchmarks.generate_dataset` · `python run_dataset.py` · `python verify_pipeline.py` |

## Source language (MiniArith)

```
input a, b;                     # read integers from the input stream
x = (a + 2) * b - a / 3;        # + - * / % and unary minus; / and % truncate toward zero (C)
print x % 5;                    # observable output
if (a < b) { print a; } else { print b; }   # staged extension: if/else, < <= > >= == !=
```

Integers are unbounded. Division or modulo by zero is an observable runtime error, and
values printed before it stay observable. Names like `t1` are reserved for compiler
temporaries. Straight-line programs are the core subset. The dataset contains only
straight-line programs; if/else is a tested extension.

## Mutation operators

| ID | Operator | Rewrite |
|---|---|---|
| M1 | Zero-Add Removal | `a+0`, `0+a`, `a-0` → `a` |
| M2 | One-Mul Removal | `a*1`, `1*a`, `a/1` → `a` |
| M3 | Constant Folding | `c1 op c2` → `c` (never folds a division by zero) |
| M4 | Strength Reduction | `a*2` → `a+a`, `a*2^k` → `a<<k` |
| M5 | Dead Assignment Removal | backward liveness; never removes `input` or a division that may trap |
| M6 | Common Subexpression Elimination | reuses an available identical expression (commutative-aware, killed by redefinition) |
| M7 | Constant Propagation | substitutes `k = c` into later uses and drops the copy if it becomes dead |
| M8 | Instruction Reordering | swaps adjacent independent instructions (no RAW/WAR/WAW; I/O and traps keep their order; never across labels or jumps) |
| M9 | Copy Propagation | substitutes `x = y` into later uses and drops the copy if it becomes dead |
| M10 | Branch Folding (CFG) | `ifz <const>` → `goto` or fall-through, plus removal of unreachable code |
| MX | Unchecked Reordering (*unsafe*) | demo only (`--unsafe`, or the dashboard toggle): shows the validator rejecting wrong candidates |

For programs with control flow, M1–M9 run one basic block at a time with a conservative
`live_out` set (see [docs/architecture.md](docs/architecture.md)).

## Strategies compared

| Strategy | Definition |
|---|---|
| unoptimized | Naive TAC from the compiler |
| fixed | Classical pass order `M3 M7 M9 M1 M2 M6 M9 M4 M5 M10` applied to a fixpoint, then an M8 scheduling pass |
| greedy | Review-1 best-improvement search |
| beam | Width 4, patience 3 |
| greedy w/o M8 | Ablation that isolates the effect of instruction reordering |

## Cost model and measurements

`cost = 0.4·I/I₀ + 0.3·A/A₀ + 0.2·T/T₀ + 0.1·C/C₀`, where I is instructions, A arithmetic
operations, T distinct temporaries and C **simulated** cycles. The cycle model is
deterministic: `*` costs 3, `/ %` cost 10, everything else 1, plus a 1-cycle stall when an
instruction reads the previous instruction's result. Simulated cycles are **not** CPU cycles.
Real time is measured separately: wall-clock medians on two Python executors, with warm-up,
interleaved repeats, raw samples saved and IQR-based improvement classification. Six
alternative weight configurations test sensitivity. See
[docs/methodology.md](docs/methodology.md).

## Correctness

- **Validator.** Candidates are tested against the original with validation protocol v3:
  the 25 Review-1 vectors, structured vectors (one input set to zero, equal pairs), and
  coverage vectors (distinct-value specials, large random values, small-range enumeration).
  Review-1 (the default) and v2 remain available unchanged.
- **Independent final check.** Every result is compared with an AST reference interpreter,
  which has its own arithmetic, on fixture tests and 200 held-out inputs, and through a
  second (compiled) backend.
- **Operator soundness.** Every operator is checked by exhaustive enumeration on hazard
  programs.
- **Further checks.** Seeded property tests, fuzzing, regression cases for every bug found,
  and a fault-injection study of the validator.

This is **testing, not proof**. Against a strong independent oracle, the validator self-test
still finds 46 missed faults out of 14,872 non-equivalent mutants for v3 (v2: 150). See
[docs/validator_investigation.md](docs/validator_investigation.md).

## Layout

| Path | Contents |
|---|---|
| `frontend/` | lexer, parser (AST, definite assignment), TAC IR + naive generator, basic blocks/CFG, canonical TAC |
| `execution/` | TAC interpreter + cycle model, AST reference interpreter, TAC→Python backend, validator, timing harness |
| `optimizer/` | mutation operators, cost model, greedy search, fixed/beam strategies |
| `benchmarks/` | Review-1 generator (unchanged), template/random generators, Jotai extractor, dataset build/dedup/stats, configs |
| `dataset/` | released dataset `miniarith-v1` + [README](dataset/README.md) |
| `jotai_unique_300/` | upstream source collection: 300 Jotai C files (GPL-3.0 collection; not benchmarks by themselves) |
| `experiments/` | runner, aggregation, validator evaluation, plots, report generator |
| `results/final/` | experiment logs (CSV/JSONL/JSON), plots, verification summary |
| `results/` (top level) | Review-1 artifacts, kept unchanged for provenance |
| `dashboard/` | local web dashboard |
| `tests/` | unit, semantics, mutation-safety, property, regression, strategy, dataset and dashboard tests; fuzzer; `regressions/` |
| `docs/` | architecture, methodology, results, report, slides outline, demo guide, limitations |
| `reproduce.py` | end-to-end pipeline |

## Licence

No licence has been chosen yet. The recommendation is in `docs/report.md` §9.
`jotai_unique_300/` and the C-derived dataset records are third-party material (a GPL-3.0
collection with unverified upstream function licences). Whatever licence is chosen, it does not
cover them. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Limitations (summary)

Straight-line integer language, plus if/else without loops. Unbounded integers. Validation
is finite testing. Measured speed-ups compare against deliberately naive TAC on Python
executors, not native code. The simulated stall model behind M8 has no measurable
counterpart on the interpreter. The external (C-derived) group has only 7 programs. Full
list: [docs/limitations.md](docs/limitations.md).
