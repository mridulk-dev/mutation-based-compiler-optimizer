# Presentation outline (final review, ~12 slides, 12–15 min + live demo)

Figures referenced below are in `results/final/plots/`. Numbers come from `docs/results.md`
and match the dashboard.

---

**1. Title.** Automated Mutation-Based Compiler Code Optimization. Team, course, guide.

**2. Problem.** Fixed pass orders are hand-tuned. Can a search over small, validated rewrites
optimise code safely, and does it beat a fixed order? Three questions: *safety*, *benefit*,
*realism*.

**3. Approach.** Pipeline diagram (`docs/architecture.md`, or the Methodology page):
source → naive TAC → [mutate → validate → cost → select]* → optimised TAC. Key idea: operators
only *propose*; the validator decides.

**4. Language and IR.**
- MiniArith with C division semantics.
- Division by zero counts as observable behaviour.
- Naive TAC example (P13: 29 instructions).
- Staged if/else with basic blocks and a CFG.

**5. Operators M1–M10.** Table. Two safety rules found by fuzzing: never delete or move a
division that may trap, and never reorder across I/O.

**6. Dataset (`11_dataset_composition.png`).**
- 795 candidates → **454 accepted**.
- Groups: A controlled 252 (incl. the unchanged Review-1 15), B randomized 195, C external 7.
- 288 Jotai functions are incompatible (mostly `unsigned`). The 7 accepted external programs
  are a small, single-source sample with unverified licences, not a real-world benchmark.
- Every program has a manifest record, hashes, seed and provenance, and expected outputs
  from an independent AST interpreter.
- Reproducible with `--check`.

**7. Correctness.**
- **2,270 / 2,270** results correct on fixtures + 200 held-out inputs + a second backend.
- Exhaustive operator tests; fuzzing finds 0 unsound proposals and 0 wrong results over
  1,690 programs.
- Validator fault injection (`10_validator_selftest.png`): the investigation found a coverage
  defect (all-equal special vectors, narrow random range) and fixed it with protocol v3. Misses
  against the strong oracle fell from 150 (v2) to 46 (v3), i.e. 99.69% detection.
  *Testing ≠ proof* (the 4242 example).

**8. Baselines and search (`01_cost_distribution_by_group.png`, `04_pairwise_scatter.png`).**
- Mean cost ≈ 0.41 for every strategy.
- Fixed = greedy on 429 / 454 programs.
- Beam is better on 200 programs, but only by 0.0009 on average, and needs 3.8× greedy's
  evaluations and 52× the fixed pipeline's (`05_search_effort.png`).

**9. Sensitivity (`06_weight_sensitivity.png`).** Robust under 5 of 6 weightings. Under
cycles-only weights, greedy is worse than fixed on 384 programs, because cost-neutral
enabling moves are never taken. This is the most interesting finding about greedy search.

**10. Simulated vs measured (`07_measured_speedup.png`, `08_cycles_vs_time.png`,
`09_reordering_effect.png`).**
- Measured speed-up over naive TAC on Python executors: greedy 2.47× on the interpreter and
  2.04× compiled (geomean); 0 regressions on the interpreter.
- Measured time tracks instruction count.
- M8 helps simulated cycles but not interpreter time. Be explicit that this is not native
  code.

**11. Dashboard.** Screenshot, then switch to the live demo (`docs/demo_guide.md`, sections
2–4).

**12. Limitations and future work.** Loops and data-flow analysis, SMT translation
validation, a native backend, fixed-width arithmetic for real C benchmarks, plateau-aware
search.

**Backup slides.**
- Cycle model details.
- Fixed pass order.
- Bug list (regression cases).
- Reproducibility (`python reproduce.py`, `verification_summary.json`).
- CI.
