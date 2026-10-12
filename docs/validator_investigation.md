# Validator investigation (final verification phase)

The first self-evaluation reported that validation protocol v2 rejected 99.42% of injected
non-equivalent faults and accepted 86 of them. This document records how those false
acceptances were investigated, which ones were caused by a genuine defect in the test-vector
protocol, what changed, and what remains. Numbers come from `results/final/validator_eval.json`,
which `python -m experiments.validator_eval` writes.

## 1. How the self-test works

`experiments/validator_eval.py` builds faults from the naive TAC of every accepted dataset
program (454 programs), using these fault types:

- **op-swap:** replace an operator (`+↔-`, `*→+`, `/→*`, `%→/`).
- **operand-swap:** swap the operands of `-`, `/` or `%`.
- **const-change:** add 1 to a constant.
- **drop-print:** delete a print.
- **swap-prints:** exchange two adjacent prints.
- **drop-trap:** delete a division that can trap.
- **wrong-var:** use the most recently defined other variable instead.
- **unsafe-reorder:** apply the MX operator.

Mutants are de-duplicated and capped at 60 per program, which gives 25,889 mutants. Each
mutant is judged by the validator exactly as it would be during search. Every mutant accepted
by at least one protocol is then compared with the original program by an **oracle**:

| Oracle | Inputs |
|---|---|
| `oracle300` | 300 held-out inputs (60 small, 240 in ±10⁵), the oracle of the first evaluation |
| `oracle_strong` | added here: exhaustive [−4, 4]ⁿ for n ≤ 3 (a 729-vector seeded sample for n > 3), plus 400 inputs in ±1000 and 400 in ±10⁶, with seeds independent of every protocol |

A mutant that the validator accepted but that an oracle input exposes is a **false
acceptance** ("miss"). Detection rate = rejected / (rejected + misses). Mutants with no
difference found are excluded from that rate. They are probably equivalent, for example
mutations of dead code, but that is not proven.

The comparison uses printed values plus the error kind. Both executors involved (the TAC
interpreter for the validator, the compiled-Python backend for the oracle) were cross-checked:
the rewritten self-test reproduces the original figures exactly (192 and 86 misses against
`oracle300`).

## 2. What was wrong: a genuine protocol defect

| Finding | Evidence | Cause |
|---|---|---|
| **Input-swap faults were invisible to the 8 special vectors** | REG-006 (RND-0168): `t1 = i1 / 100 → t1 = i2 / 100` passed every vector but differs on 240 of 300 held-out inputs | The special values (0, ±1, ±2, 7, 1000, −999) are applied to **every input at once**, e.g. `(1000, 1000, 1000)`. Swapping one input for another cannot change anything on those vectors. |
| **Random range too narrow** | REG-007 (RND-0191): `i0 % 2 → 2 % i0` passed, but differs on 236 of 300 inputs; `t8 / 100 → t8 / 101` likewise | The random vectors lie in [−100, 100], and later `x / 100`-style divisions map most of that range to 0, which masks the fault. |
| **One-input traps at untested small values** | REG-008 (RND-0009): the original traps only at `i0 = 3` | No vector used 3 for a one-input program. |
| **Small-input zero sets** | REG-009 (RND-0214): the mutant traps only at inputs such as `[1, −2]` | Random draws from [−100, 100] rarely land on small combinations. |

There was **no** bug in how errors are compared (outputs and error kind are both compared),
none in reference-versus-TAC semantics (the oracle uses a third executor and agrees), and none
in candidate generation. The defect was test-vector **coverage**.

## 3. Fix: validation protocol v3 (added; Review-1 and v2 unchanged)

`execution/validator.py` now offers three protocols:

| Protocol | Vectors | Vectors for n = 1, 2, 3, 4 |
|---|---|---|
| `review1` (default) | 8 all-equal specials + seeded random in [−100, 100], 25 total | 25, 25, 25, 25 |
| `v2` (`structured=True`) | review1 + one input zero, equal pairs | 25, 29, 34, 41 |
| `v3` | v2 + **rotated specials** (each input gets a different special value) + **8 large random** vectors in ±10⁵ + **small-range coverage** (exhaustive [−16, 16] for n = 1, exhaustive [−3, 3]² for n = 2, 40 seeded samples from [−3, 3]ⁿ otherwise) | 59, 82, 87, 94 |

`review1` and `v2` produce byte-identical vector lists to the previously committed code. A test
pins their digests (`tests/test_validator_protocols.py`), so the Review-1 artifacts and the
earlier results remain comparable. The final experiments and the dashboard use **v3**.

## 4. Results after the fix

| Protocol | Oracle | Rejected | Missed | Detection |
|---|---|---:|---:|---:|
| review1 | oracle300 | 14,591 | 192 | 98.70% |
| review1 | oracle_strong | 14,591 | **267** | 98.20% |
| v2 | oracle300 | 14,719 | 86 | 99.42% |
| v2 | oracle_strong | 14,719 | **150** | 98.99% |
| v3 | oracle300 | 14,826 | 31 | 99.79% |
| v3 | oracle_strong | 14,826 | **46** | 99.69% |

The stronger oracle shows that the earlier "86 misses" **understated** v2's false acceptances:
the true count is at least 150. v3 reduces the misses found by the strong oracle from 150 to
46. The strong oracle shares no seeds with any protocol. It does overlap in *kind* with v3's
small-range coverage (both use small inputs), so the oracle300 column is the fairer
before/after comparison: 86 → 31.

## 5. Remaining false acceptances (v3, strong oracle: 46)

| Mechanism | Count | Example (saved regression case) |
|---|---:|---|
| Thin zero set in the *original*: it traps only on sparse small inputs, and the mutant moves or removes that set | 22 (all from RND-0157) | REG-010, RND-0157: a modulo divisor `(2·i2' + 7) + (r + 2·i2')` is zero only at points such as `(i0, 1, −1)`; almost every upstream mutation shifts it |
| Thin zero set in the *mutant*: it introduces a trap on a sparse set (small or medium inputs) | 10 (2 from RND-0157) | REG-011, SYN-CSE-006: `a / b → a / u1`, where u1 = 0 only at `[−2, −2, 2]` |
| Value difference confined to a narrow input region (9 medium, 5 large witnesses), masked elsewhere by integer division | 14 | REG-012, RND-0194: `t5 * t6 → t5 + t6`, visible on 2 of 300 held-out inputs |
| Single-point blind spot (constructed) | — | `a + 1` versus a mutant that traps only at `a = 4242`: accepted by every protocol |

These are not bugs. Any finite vector set misses some inputs, so a fault whose observable
effect is confined to a set the vectors don't touch will be accepted. Adding more vectors only
shrinks this set. `tests/test_validator_protocols.py` keeps these cases as **documented known
false acceptances**: each is checked to differ genuinely at its witness, and the test fails if
a future protocol starts rejecting one, prompting this document to be updated.

## 6. Why this does not invalidate the optimisation results

All safe operators (M1–M10) proposed **zero** candidates that the validator rejected, under
every protocol, across all experiments and fuzzing. Every final program was also checked
independently of the validator: against the AST reference on the fixture tests and 200
held-out inputs, and through the compiled backend. All 2,270 results passed. The validator is
a safety net. The primary evidence of correctness is operator soundness: exhaustive tests over
[−4, 4]ⁿ, property tests and fuzzing. **No claim of formal equivalence is made.**

## 7. Limitations of the self-test itself

- The mutants are single faults applied to *naive* TAC. Faults in optimised code, or several
  faults at once, are not sampled.
- The 60-per-program cap takes mutants in generation order, so later instructions of long
  programs are under-sampled.
- "No difference found" is decided by a finite oracle (about 1,500–1,900 inputs).
- The fault types are hand-chosen. They are not a complete fault model.
