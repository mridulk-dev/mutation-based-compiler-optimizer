# Methodology

This document covers four things: how correctness is established, what each metric means,
how runtime is measured, and how the strategies are compared. The numbers live in
[results.md](results.md), which `python -m experiments.report` generates from the saved logs.

## 1. Language semantics (the specification every executor implements)

- MiniArith integers are unbounded. Overflow is not modelled, but the reference interpreter
  reports int32 violations.
- `/` truncates toward zero. `%` takes the sign of the dividend, and `a == b*(a/b) + a%b`.
  So `-7/2 = -3`, `-7%2 = -1` and `7%-2 = 1`.
- `/` or `%` by zero stops the program with the error kind `division by zero`. Values printed
  before the error are still observable.
- Reading past the end of the input stream stops the program with `input stream exhausted`.
- The **observable behaviour** of a run is the pair *(sequence of printed values, error kind
  or none)*.
- The staged extension `if (c) {…} else {…}` takes the then-branch when `c != 0`. Relational
  operators yield 1 or 0.

Three executors implement this specification:

| Executor | Role | Independence |
|---|---|---|
| `execution/reference.py` (AST) | Oracle for expected outputs and final checks | Has its own arithmetic and imports only the parser. A unit test inspects its imports. |
| `execution/interpreter.py` (TAC) | Used during search, and provides simulated cycles | Shares `apply_op` with constant folding (M3), which is why the oracle must be independent of it |
| `execution/pycodegen.py` (TAC → Python) | Second backend, used for checks and timing | Separate code generation |

## 2. Correctness protocol

1. **During search (validator).** Every candidate is run on the same test vectors as the
   original program. Its printed values and error kind must be identical on every vector,
   or the candidate is rejected and never applied.
   - *Review-1 protocol*: 8 special vectors (0, ±1, ±2, 7, 1000, −999, each repeated for
     every input) plus seeded random vectors in [−100, 100], 25 in total, seed 42.
   - *Protocol v3* (used by the final experiments and the dashboard): v2 plus coverage vectors.
     These are rotated distinct-value specials, 8 random vectors in ±10⁵, and small-range coverage
     (exhaustive [−16, 16] for one input, [−3, 3]² for two, 40 samples from [−3, 3]ⁿ otherwise).
     See [validator_investigation.md](validator_investigation.md).
   - *Protocol v2*: the 25 vectors above, plus structured vectors. These set one input to 0
     with the others non-zero and of mixed sign,
     and make every pair of inputs equal (with both signs). The validator self-test below
     motivated this addition.
2. **After search (independent of the validator).** Each final program is compared with the
   AST reference on the dataset's fixture tests, whose expected outputs come from the
   reference and are stored in the manifest. It is also compared on **200 held-out inputs**:
   40 small values and 160 values drawn from [−10⁶, 10⁶], using a different seed. Finally,
   the compiled-Python backend must agree on the same held-out inputs. A result counts as
   *correct* only if all three checks pass.
3. **Operator soundness, without the validator.** `tests/test_mutation_safety.py` enumerates
   every input vector in [−4, 4]ⁿ, which always includes zero divisors. It checks every
   candidate of every operator on hazard programs: redefinition, RAW/WAR/WAW dependencies,
   I/O order, trapping dead divisions, CSE kills, copy-source redefinition, constant errors,
   negative strength reduction, dead chains, and control flow. A meta-test confirms that the
   harness does catch the unsafe operator MX.
4. **Seeded property tests** (`tests/test_property.py`) cover random programs, both
   straight-line and if/else. They check that every single mutation preserves behaviour, that
   all strategies preserve behaviour on unseen inputs, that the three executors agree, that
   TAC text round-trips, that canonical TAC is invariant under renaming, and that strategies
   are deterministic. `hypothesis` is not a dependency, so the properties are checked over
   fixed-seed samples.
5. **Fuzzing workload** (`tests/fuzz.py`) is kept separate from the benchmark. It fuzzes
   random straight-line and if/else programs with every strategy and checks the optimised
   programs against the reference on 150 fresh inputs. Any failing program is saved under
   `tests/regressions/found/` together with its seed.
6. **Regression cases** (`tests/regressions/cases.json`): every bug found becomes a case that
   `tests/test_regressions.py` replays.
7. **Validator self-evaluation** (`experiments/validator_eval.py`) injects faults into every
   accepted program: operator swap, operand swap, changed constant, dropped print, swapped
   prints, dropped trapping division, wrong variable, and unchecked reordering. Each mutant is
   judged by the validator and also compared on 300 held-out inputs. Both protocols are
   evaluated.

**Finite differential testing is not a proof of equivalence.** The self-evaluation measures
how often the validator misses, and a constructed example shows a mutant it cannot catch
(one that fails only when `a == 4242`). No formal equivalence checking was implemented.
Exhaustive enumeration over [−4, 4]ⁿ covers only that small domain.

## 3. Cost model (simulated)

`cost = wI·I/I₀ + wA·A/A₀ + wT·T/T₀ + wC·C/C₀`. The `₀` subscript marks the unoptimised
program, so its cost is 1.0. The baseline weights are `wI=0.4, wA=0.3, wT=0.2, wC=0.1`, the
Review-1 values, kept for comparability.

| Metric | Definition |
|---|---|
| I: TAC instructions | All instructions except `label` pseudo-instructions |
| A: arithmetic operations | Instructions computing `+ - * / % << neg` or a comparison |
| T: distinct temporaries | Distinct `t<k>` names defined |
| C: simulated cycles | Mean over the validation vectors of the deterministic cycle model: `+ - << copy neg io compare goto ifz` = 1, `*` = 3, `/ %` = 10, `label` = 0, plus 1 stall cycle when an instruction reads the value written by the previously *executed* instruction. **These are not CPU cycles.** |

## 4. Wall-clock runtime (measured)

- **Backends.** (a) The Python TAC interpreter, whose cost per instruction is dominated by
  dispatch overhead. (b) TAC compiled into a Python function, which has no dispatch overhead
  but still runs Python big-integer operations. Native code is *not* measured.
- **Protocol** (`execution/timing.py`). Each program's variants (unoptimised, fixed, greedy,
  beam, greedy without M8) are timed together on the same batch: that program's error-free
  fixture inputs. Each variant gets 3 warm-up runs, then 15 timed rounds. Within a round the
  variants run in an interleaved, rotating order, so drift spreads evenly across them. The
  garbage collector is disabled while timing. Each sample is the total nanoseconds
  (`perf_counter_ns`) for one pass over the batch, divided by the batch size.
- **Phase separation.** The search and correctness phase runs in parallel worker processes.
  Timing runs afterwards, sequentially, in the main process, so measurements do not compete
  with search work.
- **Reporting.** Raw samples go to `timing_raw.csv`, and the median, Q1, Q3, IQR and minimum
  to `timing.csv`. Per-program speed-up is median(unoptimised) / median(strategy), aggregated
  by geometric mean. A program counts as *improved* only when the strategy's Q3 is below the
  unoptimised Q1, and *regressed* in the mirror case. Everything else is *inconclusive*.
- **Is the model predictive?** Spearman rank correlation between simulated cycles (or
  instruction count) and measured time is computed over all (program, strategy) points.
- **Noise and threats.** Python interpreter overhead, CPU frequency scaling, background
  processes and OS scheduling all affect these numbers. Timing is the only non-deterministic
  output, and repeat runs give slightly different values.

## 5. Baselines and search strategies

All strategies use the same operators (`optimizer/mutations.py`), the same validator
(protocol v3, seed 42), the same cost function and the same programs.

| Strategy | Definition |
|---|---|
| `unoptimized` | Naive TAC from the compiler |
| `fixed` | Classical pass pipeline with `FIXED_ORDER = M3, M7, M9, M1, M2, M6, M9, M4, M5, M10`. Each pass applies its first valid candidate, **regardless of cost**, until it has no candidates left. The whole sequence repeats until a full round changes nothing (at most 50 rounds or 5,000 steps). A final M8 scheduling pass then accepts only swaps that lower simulated cycles. |
| `greedy` | Review-1 best-improvement search: each step takes the cheapest strictly improving valid candidate, and the search stops at a local optimum |
| `beam` | Width 4. At each depth, all mutations of all beam programs are generated, deduplicated against every program seen so far, and validated. The 4 cheapest are kept (a stable sort, so runs are deterministic), and the best-ever program is tracked. The search stops after 3 depths without a new best, or at 50,000 evaluations |
| `greedy_noM8` | Ablation: greedy without instruction reordering, used to isolate M8's effect |

Every strategy has a time limit of 120 s per program, and timeouts are counted. Failures and
crashes are recorded in the result rows, never dropped.

**Reported for every strategy:** correctness pass rate; distribution of final cost (mean,
median, P10, P90, box plots); per-metric change; per-program win/tie/loss against the other
strategies, with the programs where a strategy is worse listed by id; candidates evaluated
and rejected; programs executed; search time; timeouts; and measured time. Results are
broken down by dataset group and category, and groups are never pooled into one average.

## 6. Cost-weight sensitivity

`fixed` and `greedy` are re-run under 6 weight configurations (`optimizer/cost.py:
WEIGHT_CONFIGS`): baseline, uniform, size-heavy, cycles-heavy, cycles-only, and no-temps.
For each configuration the report gives the raw metric changes and greedy-versus-fixed
win/tie/loss counts. It also records whether the direction of the greedy-versus-fixed
comparison matches the baseline configuration. Weighted costs from different configurations
are not comparable with each other, but raw metric changes are.
