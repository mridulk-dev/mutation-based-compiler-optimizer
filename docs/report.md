# Automated Mutation-Based Compiler Code Optimization

*Final project report (draft for submission). Course: Compiler Design Lab, VIT.*
*All quantitative statements come from the committed artifacts: `dataset/miniarith-v1/` and
`results/final/`. [results.md](results.md) is the full table set, generated from those logs.*

## Abstract

We build a search-based optimiser for three-address code (TAC). A compiler for MiniArith, a
small integer language, emits deliberately naive TAC. Ten mutation operators then propose
local rewrites: algebraic simplification, constant folding and propagation, strength
reduction, dead-code elimination, common-subexpression elimination, copy propagation,
instruction reordering and branch folding. A differential-testing validator accepts a
candidate only if its printed output and runtime-error behaviour match the original program
on a fixed set of input vectors. Three strategies use the same operators, validator and cost
function: a fixed-order pass pipeline, greedy best-improvement search and beam search. We
compare them on a reproducible dataset of **454 validated programs** in three separately
reported groups: controlled, randomized, and C-derived from Jotai.

All **2,270 (program, strategy) results** match an independent AST reference interpreter on
the fixture tests and on 200 held-out inputs per program. On the simulated cost model, every
strategy lowers mean cost to about 0.41 of the naive TAC. The fixed pipeline and greedy
search tie on 429 of 454 programs. Beam search finds slightly cheaper programs on 200
programs, but the mean improvement is only 0.0009, and it needs about 3.8× more candidate
evaluations than greedy and about 52× more than the fixed pipeline. Measured with Python
executors, the optimised programs run about 2.0–2.5× faster than the naive TAC (geometric mean).
On the interpreter, measured time correlates with instruction count (ρ = 0.82) more strongly
than with simulated cycles (ρ = 0.76). Instruction reordering lowers simulated cycles but has
no established real-world benefit: it is slightly harmful on the interpreter and slightly
helpful on the compiled backend, for reasons unrelated to the stall model. A fault-injection study found a coverage defect in the validator's test vectors.
After the fix (protocol v3), a strong independent oracle still finds 46 accepted faults out of
14,872 non-equivalent mutants (99.69% detection). Correctness is therefore established by
testing, not proof.

## 1. Introduction and objectives

Classical optimisers apply passes in a fixed order chosen by the compiler writer.
Search-based optimisation instead treats each rewrite as a move in a search space and lets
a cost function choose. That raises three questions, and this project answers each with
measurements on the same corpus:

1. **Safety.** How do we make sure that no accepted rewrite changes behaviour, including
   runtime errors such as division by zero?
2. **Benefit.** Does search beat a reasonable fixed pass order, and at what cost in search
   effort?
3. **Realism.** Do improvements on a simulated cost model show up as measured runtime?

Objectives for this release:

- a validated, reproducible benchmark of 300–500 programs;
- rigorous correctness testing;
- fair baselines;
- separation of simulated and measured performance;
- a dashboard connected to the real compiler;
- a staged control-flow extension.

## 2. System overview

See [architecture.md](architecture.md) for component and workflow diagrams.

- **Language.** MiniArith has `input`, assignment, `print`, `+ - * / %` with C truncation,
  and unary minus. The staged extension adds `if/else` with relational conditions. Division
  by zero is an observable runtime error.
- **Frontend.** A recursive-descent parser enforces definite assignment, so programs never
  read undefined variables. The naive TAC generator puts every operator result in a fresh
  temporary and turns every assignment into a copy. For control flow it emits
  `ifz`/`goto`/`label`, and `basic_blocks()` builds the CFG.
- **Operators M1–M10** (see the README table). For if/else programs the local operators run
  on one basic block at a time with a conservative `live_out` set, and M10 folds constant
  branches and removes unreachable code.
- **Validator.** Candidates run under protocol v3: the 25 Review-1 vectors, structured
  vectors, and coverage vectors (distinct-value specials, large random values, small-range
  enumeration). Printed values and the error kind must be identical to the original program's.
- **Cost.** `0.4·I/I₀ + 0.3·A/A₀ + 0.2·T/T₀ + 0.1·C/C₀`, where C is simulated cycles from a
  deterministic in-order model with a one-cycle read-after-write stall.
- **Strategies.** A fixed pipeline (`M3 M7 M9 M1 M2 M6 M9 M4 M5 M10` to a fixpoint, then an
  M8 scheduling pass), greedy search, and beam search with width 4. Greedy without M8 serves
  as an ablation.

## 3. Dataset

Full details are in [dataset/README.md](../dataset/README.md).

| Group | Source | Candidates | Accepted |
|---|---|---:|---:|
| A controlled | 15 Review-1 programs (unchanged) + 240 template programs in 6 categories with recorded size and redundancy | 255 | 252 |
| B randomized | grammar-based generator with a recorded config; not targeted at any operator | 240 | 195 |
| C external | conservative C-subset translation of 300 Jotai functions, cross-checked with gcc | 300 | 7 |
| **Total** | | **795** | **454** |

Each accepted program has 3–15 test vectors whose expected outputs come from the AST
reference interpreter, 4,487 tests in total, 199 of which expect a runtime error. Each
program also has SHA-256 hashes of its source, normalised source, TAC and canonical TAC, plus
its provenance (generator, version, seed and config, or upstream file, hash and function),
its licence status, and its validation checks. Three executors (AST reference, TAC
interpreter, TAC compiled to Python) must agree before a program is accepted.

**Rejections.** 39 programs always fail at runtime, and 9 exceed the int64 range.
**Duplicates.** 5 Jotai functions are identical after normalisation (e.g. `129 + row`
appears three times). **Incompatible.** 288 Jotai files use features outside the subset:
`unsigned` 166, branches 39, pointers 21 and so on.

All 7 accepted external records come from functions in **one** upstream file (path hint:
`…/libiconv-1.15/tools/cjk_tab_to_h.c`). Group C is therefore not a diverse sample of real-world
code.

The build is deterministic, and `python -m benchmarks.build --check` regenerates the manifest
byte for byte. `python -m benchmarks.audit` independently recomputes every count, hash, TAC
listing and expected output, and checks Review-1 identity against the pre-project commit. The C-derived fragments are GPL-3.0 at collection level, and their upstream
licences are *unverified*.

## 4. Correctness methodology

See [methodology.md](methodology.md) §2. In short:

- **Validator at search time.** The validator decides which candidates the search may apply.
- **Independent final checks.** Every result is checked against the AST reference on the
  fixture tests and 200 held-out inputs, and through the compiled backend.
- **Operator soundness.** Exhaustive enumeration over [−4, 4]ⁿ on hazard programs, including
  control flow.
- **Seeded property tests, fuzzing and regressions.** Every bug found becomes a regression
  case.
- **Validator fault injection** (§5.1).

Bugs found and fixed during this work (all now regression cases):

1. A jump to an undefined label crashed the TAC interpreter, which would have aborted a
   search. It now reports a runtime error.
2. A negated constant printed as `t = -5` was re-parsed as a copy of −5. The TAC text format
   now writes `-(5)`. This matters because timing re-parses TAC text.
3. The Review-1 validator vectors missed faults that appear only when exactly one input is 0
   or two inputs are equal. Protocol v2 adds structured vectors for these cases.
4. *(Final verification phase.)* The 8 special vectors gave every input the **same** value,
   so a fault that swaps one input for another was invisible to them. The random range
   [−100, 100] was also masked by later `x / 100` divisions, so some faults visible on about
   80% of inputs passed validation. Protocol v3 adds rotated distinct specials, large random
   vectors and small-range coverage (`docs/validator_investigation.md`; regression cases
   REG-006 to REG-009).
6. *(Final verification phase.)* The experiments used a 120 s wall-clock limit per search.
   On a slowed machine this cut two beam searches short, so the results depended on machine
   speed. Experiments now use deterministic budgets only, and a test guards this.
7. *(Final verification phase.)* `dirty_worktree` was read after the pipeline had rewritten
   its own tracked result files. Provenance is now captured at the start of a run.
8. The two Review-1 fuzzing findings, about trapping divisions under M5 and M8, are kept as
   regression cases.

## 5. Results

### 5.1 Correctness

| Check | Result |
|---|---|
| (program, strategy) results correct: fixture tests, 200 held-out inputs, compiled backend | **2,270 / 2,270** |
| Fixture tests passed (per strategy) | 4,487 / 4,487 |
| Candidates rejected by the validator (safe operators) | 0 |
| Crashes / timeouts | 0 / 0 |
| Validator fault injection, protocol v3 (strong oracle) | 14,826 of 14,872 non-equivalent mutants rejected (**99.69%**); **46 accepted** |
| Validator fault injection, protocol v2 (strong oracle) | 98.99%; 150 accepted (86 against the original 300-input oracle) |
| Validator fault injection, Review-1 protocol (strong oracle) | 98.20%; 267 accepted |
| Constructed blind spot (fails only at a = 4242) | accepted by the validator (expected; testing is not proof) |
| Fuzzing workload (`reproduce.py`) | 1,300 straight-line programs with greedy (20,903 accepted mutations), 130 with fixed and beam (4,127), 260 if/else programs with greedy and fixed (10,151): **0 unsound proposals, 0 wrong results** |

The safe operators never proposed an invalid candidate. The validator therefore never had to
filter a wrong rewrite in the experiments, and the main safety evidence is operator
soundness, which the exhaustive and property tests check.

### 5.2 Optimisation: simulated cost model

Mean final cost (unoptimised = 1.0; lower is better), per group:

| Group | n | Fixed | Greedy | Beam | Greedy w/o M8 |
|---|---:|---:|---:|---:|---:|
| A controlled | 252 | 0.4078 | 0.4078 | 0.4071 | 0.4117 |
| B randomized | 195 | 0.3986 | 0.3986 | 0.3975 | 0.4020 |
| C external | 7 | 0.9987 | 0.9987 | 0.9987 | 0.9991 |

Per-metric changes (all groups, mean per program): instructions −57.1%, arithmetic ops
−58.7%, temporaries −58.7%, simulated cycles −65.2% (fixed) / −65.2% (greedy) / −66.1%
(beam). In groups A and B every program improved. In group C only 1 of 7 did, because these
are single-expression functions such as `print 33 + col` with nothing to optimise.

**Per-program comparisons** (no averaging):

- **Greedy vs fixed:** greedy better on 10, tied on 429, worse on 15. Every difference is at
  most 0.0026 in cost. Instruction, arithmetic and temporary counts are identical on all 454
  programs, so every difference is in simulated cycles, i.e. in the instruction schedule.
- **Beam vs greedy:** beam better on 200, tied on 253, worse on 1 (SYN-STR-012, +0.0011).
- **Greedy vs greedy w/o M8:** greedy better on 360 programs, tied on 94, through simulated
  cycles only (mean −2.9 cycles).

**Search effort.** Mean candidate evaluations per program are 48 for fixed, 663 for greedy
and 2,506 for beam. Total search time across all programs is 73 s, 1,340 s and 5,002 s
respectively (CPU time summed over 11 parallel worker processes, final run, protocol v3).
No strategy has a wall-clock cutoff; every search ran to its deterministic stopping rule
(0 timeouts).

**Interpretation.** For this operator set and naive TAC, a well-chosen fixed pass order
already reaches the greedy optimum on almost every program, at a fraction of the cost. Beam
search's extra exploration buys only marginal cycle savings from better instruction
schedules.

### 5.3 Cost-weight sensitivity

Greedy and fixed were re-run under six weightings. For baseline, uniform, size-heavy,
cycles-heavy and no-temps weights, the raw metric changes are almost identical and greedy ≈
fixed. Under **cycles-only** weights, greedy is **worse than fixed on 384 of 454 programs**
(mean cost 0.455 vs 0.348; instruction reduction −40.9% vs −57.1%). The reason is that
greedy accepts only strictly improving moves. Enabling moves such as CSE (`t2 = a+b` →
`t2 = t1`, a copy costing the same one cycle) or copy propagation that leaves a live copy
are cost-neutral under cycles-only, so greedy stops early. A fixed pipeline applies them
unconditionally. **The conclusion "greedy ≈ fixed" therefore depends on a cost function that
rewards intermediate steps.**

### 5.4 Measured runtime

Measured on Python 3.13.3, Windows 11, with 15 interleaved repeats after 3 warm-up runs;
median per program. Speed-up is the median time of the naive TAC divided by the median time
of the optimised program.

| Strategy | Interpreter: geomean speed-up | improved / inconclusive / regressed | Compiled: geomean speed-up | improved / inconclusive / regressed |
|---|---:|---:|---:|---:|
| Fixed | 2.43× | 422 / 32 / 0 | 1.98× | 415 / 29 / 10 |
| Greedy | 2.47× | 425 / 29 / 0 | 2.04× | 418 / 28 / 8 |
| Beam | 2.47× | 425 / 29 / 0 | 2.02× | 414 / 30 / 10 |
| Greedy w/o M8 | 2.48× | 421 / 33 / 0 | 1.96× | 411 / 32 / 11 |

- Correlation with measured time (Spearman ρ, all program × strategy points): simulated
  cycles 0.763 (interpreter) / 0.760 (compiled); instruction count
  0.825 / 0.719.
- Instruction reordering: among the 360 programs where M8 lowered simulated cycles, the
  variant with fewer simulated cycles was also measured faster in 130 of them (36%) on the
  interpreter and in 333 (92.5%) on the compiled backend. Across three full runs these rates
  were 40% / 32.5% / 36% (interpreter) and 76% / 95% / 92.5% (compiled).
- The external group shows no meaningful change (geomean 0.97–1.06× across strategies and
  backends), consistent with its simulated cost.

**Interpretation.** The optimised programs are measurably faster than naive TAC on both
Python executors: 425 of 454 programs improve under greedy on the interpreter, 29 are
inconclusive, and none regress.
The gain comes mostly from executing fewer instructions, since measured time correlates more
strongly with instruction count than with simulated cycles on the interpreter. For instruction
reordering the two executors disagree:
- On the **interpreter**, the variant with fewer simulated cycles was *slower* in most
  programs. Greedy without M8 even has a marginally higher geomean
  (2.481× vs 2.468× at three decimals).
- On the **compiled** backend, reordered programs were faster in most programs
  (2.04× vs 1.96×). That agreement rate varied
  between runs (76%, 95%, 92.5%).

The mechanism is not the simulated stall (CPython has no such hazard); it is presumably a
bytecode-level effect, which we have not analysed. Instruction reordering therefore has **no
established real-world benefit** here. No claim is made about native code.

### 5.5 Example

P13 (Review-1, mixed category) shrinks from 29 to 14 TAC instructions and from 74 to 20
simulated cycles through 28 accepted transformations. The full trace and side-by-side TAC
are in [results.md §6](results.md#6-representative-beforeafter-tac).

## 6. Control-flow extension

if/else compiles to `ifz`/`goto`/`label` and is split into basic blocks with a CFG. Local
operators work per block with a conservative `live_out` set, and M10 folds constant branches
and deletes unreachable blocks. The extension is covered by exhaustive soundness tests on
seven control-flow hazard programs, by property tests, and by fuzzing (300 random if/else
programs × 3 strategies during development: 0 unsound proposals, 0 wrong results). It is not
part of the benchmark dataset. Loops, and therefore iterative data-flow analysis, are not
implemented.

## 7. Dashboard

`python -m dashboard.server` serves a local single-page application built on the standard
library HTTP server and vanilla JS, with no CDN. It has five pages:

- **Overview:** dataset and results headline numbers, head-to-head comparisons, and the plot
  gallery.
- **Compiler:** live compile and optimise with selectable strategies, weights and the unsafe
  operator; original and optimised TAC side by side; the trace and rejected candidates;
  before/after metrics with measured time; and the correctness table.
- **Dataset explorer:** filter, sort and inspect any record, with downloads.
- **Experiments:** per-group and per-category tables, comparisons with regression links,
  sensitivity, timing, and per-program drill-down.
- **Methodology.**

Submitted text only ever reaches the MiniArith parser, requests are size- and time-limited,
and downloads are whitelisted. API tests and the reproducibility pipeline check that the
dashboard's numbers equal the artifact files.

## 8. Reproducibility

`python reproduce.py` checks the environment, then:

1. rebuilds and compares the dataset, and runs the independent dataset audit;
2. re-runs deduplication;
3. runs the 121 tests;
4. fuzzes 1,300 straight-line programs with greedy, 130 with fixed and beam, and 260 if/else
   programs with greedy and fixed;
5. re-runs every experiment and the validator study;
6. regenerates the plots;
7. checks that the dashboard API agrees with the files;
8. writes `results/final/verification_summary.json`.

Everything except timing is deterministic. CI (GitHub Actions) runs the tests, the dataset
check, the Review-1 verifier and small fuzzing runs.

The final run passed all eight steps in 2,125 s on commit `de6cdc5`, with no uncommitted
changes (`dirty_worktree: false`) and validation protocol v3. Two earlier full runs are archived
in `results/history/` and are not cited:
- the v2 run at `cd396dc`, superseded by the protocol change;
- the v3 run at `ede46c9`, where a 120 s wall-clock limit cut two beam searches short on a
  slowed machine. Experiments now use deterministic budgets only.

## 9. Licensing recommendation

The repository has no root `LICENSE` file. Recommendation (not legal advice):

- **Project code and the project-generated data:** the compiler, optimiser, experiments,
  dashboard and dataset groups A and B. Use **MIT**: short, permissive and common for
  academic code. Apache-2.0 is an equally good choice if an explicit patent grant is wanted.
- **Documentation and figures:** optionally CC BY 4.0. MIT is also acceptable.
- **Explicit exclusion.** The licence text, or a NOTICE next to it, must state that
  `jotai_unique_300/` and the group-C records (`EXT-JOTAI-*`) are third-party material. They
  are **not** covered by the project licence. They come from a GPL-3.0 collection, and their
  upstream function licences are unverified (see `THIRD_PARTY_NOTICES.md`).
- **Single-licence alternative.** If the authors prefer one licence for everything, GPL-3.0
  is the only option that matches the Jotai collection's declared licence.

## 10. Limitations and future work

See [limitations.md](limitations.md). The main points:

- the language is straight-line plus acyclic if/else;
- integers are unbounded;
- validation is testing, not formal verification;
- simulated cycles are a didactic model;
- speed-ups are measured on Python executors against deliberately naive TAC;
- the C-derived group is tiny;
- the template group is optimiser-friendly by construction.

Future work: loops and iterative data-flow analysis, SMT-based translation validation, a
native backend with hardware counters, fixed-width arithmetic (which would unlock much more
of Jotai and ExeBench), and search strategies with plateau or enabling moves.

## References

- C. C. Kind, M. Canesche, F. M. Q. Pereira. *Jotai: a Methodology for the Generation of
  Executable C Benchmarks.* Tech. Report 02-2022, UFMG, 2022.
  <https://github.com/lac-dcc/jotai-benchmarks>
- A. V. Aho, M. S. Lam, R. Sethi, J. D. Ullman. *Compilers: Principles, Techniques, and Tools*,
  2nd ed., Addison-Wesley, 2006 (TAC, basic blocks, local optimisations, data-flow analysis).
