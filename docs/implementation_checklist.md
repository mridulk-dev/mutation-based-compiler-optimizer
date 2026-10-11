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

- [x] Phase 1 – audit, baseline tests recorded, feature branch
- [ ] Phase 2 – independent reference arithmetic, configurable cost weights, expanded semantic/mutation-safety/property tests, regression-case directory, validator self-evaluation
- [ ] Phase 3 – dataset v1: controlled (Review-1 15 kept unchanged + expanded templates), randomized, external (Jotai, conservative extraction, gcc cross-check); manifest, dedup, validation, statistics, README
- [ ] Phase 4 – baselines (unoptimized, fixed-order pipeline, greedy, beam), wall-clock timing, sensitivity analysis, per-group reporting, plots
- [ ] Phase 5 – local web dashboard connected to the real compiler and artifacts
- [ ] Phase 6 – beam search; staged `if/else` + basic blocks + CFG with block-local optimisation (only if phases 2–5 are done)
- [ ] Phase 7 – docs, report draft, slides outline, demo guide, reproducibility script, CI, commits
