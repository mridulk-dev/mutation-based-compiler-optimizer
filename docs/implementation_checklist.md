# Implementation checklist (final submission)

Branch: `feature/final-submission` (created from `main` @ `8dfe1e5`, clean tree).

## Audit findings (start of session)

| Area | State found |
|---|---|
| Compiler frontend (`frontend/`) | Working lexer, recursive-descent parser, naive TAC generator, TAC text parser. Straight-line MiniArith only. **Note:** `frontend/` is the *compiler* frontend; there was no web UI. |
| Execution (`execution/`) | TAC interpreter + deterministic cycle model; differential validator (25 vectors, seed 42); AST reference interpreter. The reference re-used `apply_op` from the TAC interpreter, so arithmetic was not fully independent. |
| Optimizer (`optimizer/`) | 9 safe operators (M1–M9) + 1 unsafe demo operator (MX); fixed-weight cost model; greedy best-improvement search. |
| Dataset | 15 seeded Review-1 programs (`benchmarks/dataset/review1_dataset.json`), 7 hand-written `.src` benchmarks, `jotai_unique_300/` = 300 raw C files from Jotai (source collection only, not MiniArith). |
| Tests | 36 unittest tests pass (6.7 s); `verify_pipeline.py` passes on 7 benchmarks; `tests/fuzz.py` fuzzer. |
| Experiments | `run_dataset.py` (15 programs, 1.4 s), Review-1 plots and CSV/JSON in `results/`. |
| Missing | Fixed-pass baseline, beam search, wall-clock timing, weight sensitivity, dataset manifest/dedup/statistics at scale, external-source extraction, web dashboard, control flow, CI, reproducibility script, report. |

Baseline commands: `python -m unittest discover -s tests -t .`, `python verify_pipeline.py`,
`python -m tests.fuzz 1000 77`, `python -m benchmarks.generate_dataset`, `python run_dataset.py`, `python main.py`.

## Checklist

- [x] Phase 1 – audit, baseline tests recorded (36 tests OK, verify_pipeline OK), feature branch
- [x] Phase 2 – independent reference arithmetic; configurable cost weights; semantics, mutation-safety
      (exhaustive), property, regression, strategy, dataset and dashboard tests (109 total); validator
      self-evaluation and protocol v2; 3 bugs found and fixed as regression cases
- [x] Phase 3 – dataset miniarith-v1: 795 candidates, 454 accepted (252 controlled incl. Review-1 15
      unchanged, 195 randomized, 7 external); manifest, dedup, validation, statistics, README; `--check`
- [x] Phase 4 – unoptimized / fixed / greedy / beam / greedy-w/o-M8 on all 454 programs; held-out
      correctness checks; wall-clock timing on two backends; 6-config sensitivity; plots; generated report
- [x] Phase 5 – local dashboard (overview, compiler, dataset, experiments, methodology) on real
      compiler calls and artifacts; API tests; screenshots checked at desktop and narrow widths
- [x] Phase 6 – beam search; if/else + basic blocks + CFG + block-local optimisation + M10 (tested;
      not in the dataset); loops / iterative data-flow NOT implemented
- [x] Phase 7 – docs (architecture, methodology, results, report, slides outline, demo guide,
      limitations), reproduce.py, GitHub Actions CI, commits on feature/final-submission

Not done / out of scope: formal equivalence checking, native backend, loops, ExeBench integration
(investigated only conceptually; not downloaded), a LICENSE file (the authors' decision).
