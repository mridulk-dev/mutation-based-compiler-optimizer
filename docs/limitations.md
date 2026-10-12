# Limitations, threats to validity, and future work

## Implemented vs. planned

| Feature | Status |
|---|---|
| Straight-line MiniArith compiler, TAC, operators M1–M9, greedy search | Implemented; Review-1 core, unchanged in behaviour |
| Independent AST reference interpreter (own arithmetic) | Implemented and tested for independence |
| Dataset `miniarith-v1` (454 accepted programs, 3 groups, manifest, dedup, statistics) | Implemented, reproducible (`benchmarks.build --check`) |
| Fixed-order pipeline, beam search, M8 ablation | Implemented; same operators, validator and cost function |
| Wall-clock timing (2 Python backends), sensitivity analysis, plots, generated report | Implemented |
| Validator fault-injection study; validation protocol v2 | Implemented |
| if/else, basic blocks, CFG, block-local optimisation, branch folding (M10) | Implemented as a staged extension and tested; **not** in the benchmark dataset |
| Loops, functions, arrays, pointers, unsigned / fixed-width arithmetic | **Not implemented** |
| Global data-flow analysis (iterative liveness and reaching definitions over cyclic CFGs) | **Not implemented**; not needed for acyclic if/else, which uses conservative `live_out` |
| Formal equivalence checking (SMT or translation validation) | **Not implemented**. No formal verification is claimed |
| Native code generation and native timing | **Not implemented** |
| Simulated annealing / genetic search | Not implemented (beam search was implemented instead) |

## Threats to validity

**Correctness**
- Differential testing is finite. The validator self-test shows that protocol v2 misses 86
  of 14,805 injected non-equivalent faults, about 0.6%, and a constructed mutant that fails
  only at `a = 4242` is accepted. All safe operators produced zero invalid candidates, so no
  accepted result in the experiments depended on the validator catching a fault. Soundness
  rests on the operator tests, which use exhaustive enumeration only over [−4, 4]ⁿ, and on
  fuzzing.
- The "accepted, equivalent" outcome in the self-test means no difference was found on 300
  held-out inputs. It does not mean equivalence was proven. Switching protocols moved 22
  mutants out of that category, which shows the ground-truth proxy is itself imperfect.
- The TAC interpreter and constant folding (M3) share `apply_op`. A bug there could make the
  search-time validator agree with a wrong fold. The final checks guard against this because
  they use the independent AST reference and the compiled backend.

**Construct validity (metrics)**
- The simulated cycle model is a didactic in-order model with fixed latencies and a one-cycle
  stall. Its cycles are not CPU cycles.
- The cost weights are a design choice. Sensitivity analysis shows that conclusions about
  greedy versus fixed hold for most weightings, but **not under cycles-only weights**. There,
  greedy cannot take cost-neutral "enabling" moves such as CSE-to-copy, and it ends up much
  worse than the fixed pipeline.
- Measured speed-ups compare against **deliberately naive** TAC, with one temporary per
  operator and one copy per assignment. Most of the gain is the removal of redundancy that
  any standard compiler would never emit. The measurements run on Python executors, where
  per-instruction dispatch dominates, so on the interpreter speed-ups track instruction count
  (Spearman ρ = 0.91, against 0.85 for simulated cycles). They say nothing about native code.
- M8 lowers simulated cycles on 360 programs. On the interpreter, the variant with fewer
  simulated cycles was faster in only 32.5% of those programs (40% in an earlier run), so M8
  is neutral to slightly harmful there. On the compiled backend the figure is 95% (76% in an
  earlier run), but the effect is small (geomean 2.037× vs 1.946×) and its mechanism is
  unknown. It is not pipeline stalls. The run-to-run variation itself shows how fragile
  small timing effects are.

**Internal validity (timing)**
- Timings are noisy and vary with CPU frequency scaling, background processes and the OS
  scheduler. Mitigations are warm-up runs, interleaved rotating order, a disabled GC,
  medians, IQR-based classification, a sequential timing phase and raw samples kept. Repeat
  runs will give slightly different numbers.

**External validity (dataset)**
- 252 of 454 programs come from templates designed to contain the opportunities the
  operators target, so they favour the optimiser by construction. The randomized group (195)
  is less biased, but its programs are still synthetic.
- Only 7 programs come from real code, and they are trivial, single-expression functions,
  because 288 of 300 Jotai functions use features outside MiniArith (`unsigned`, pointers,
  branches, loops). Results for group C carry little weight.
- The upstream licences of the C-derived functions are unverified. The Jotai collection is
  GPL-3.0 at repository level.

## Future work

1. Loops with a cyclic CFG, iterative data-flow analysis (liveness, reaching definitions),
   and loop-invariant code motion.
2. Translation validation with an SMT solver (for example Z3) for the straight-line subset,
   to turn tested equivalence into proved equivalence for individual rewrites.
3. A native backend (TAC → C or LLVM IR) with hardware performance counters, so cost-model
   predictions can be compared with real CPU behaviour.
4. Fixed-width (int32/uint32) semantics. This would let far more of Jotai/ExeBench be
   translated; ExeBench was investigated conceptually but not downloaded or integrated.
5. Search strategies able to take cost-neutral enabling moves: plateau moves, simulated
   annealing, or macro-operators.
6. Learned or tuned cost weights, calibrated against measured time.
