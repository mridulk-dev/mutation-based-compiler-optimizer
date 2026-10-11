# MiniArith benchmark dataset — release `miniarith-v1`

A reproducible benchmark of programs in **MiniArith**, the straight-line integer language
accepted by this project's compiler. Every record was executed and validated by the builder,
so the accepted count comes from validation, not from a generation target.

| | Count |
|---|---:|
| Candidate records | 795 |
| **Accepted (usable benchmarks)** | **454** |
| Rejected (failed a validation rule) | 48 |
| Duplicates (kept, marked, not counted) | 5 |
| Incompatible external C functions (kept, with reason) | 288 |

Accepted programs by group (reported separately in all results — never averaged together):

| Group | Subgroup | Accepted | Notes |
|---|---|---:|---|
| A. controlled | `review1` | 15 | The Review-1 programs P01–P15, imported **unchanged** (source, ids, tests) |
| A. controlled | `synthetic-template` | 237 | 6 categories × 40 candidates; 3 rejected (constant chains that always divide by zero) |
| B. randomized | `random-grammar` | 195 | 240 candidates; 36 always fail, 9 exceed the int64 range |
| C. external | `jotai-anghaleaves` | 7 | 12 of 300 Jotai C functions translate; 5 of those are duplicates |

All numbers above are copied from [`miniarith-v1/statistics.json`](miniarith-v1/statistics.json), which
the builder regenerates.

## Files

| File | Contents |
|---|---|
| `miniarith-v1/manifest.jsonl` | One JSON record per candidate, any status (schema below) |
| `miniarith-v1/statistics.json` | Counts by group, category, status, rejection reason, TAC length, operator opportunity, licence status, uniqueness |
| `miniarith-v1/statistics_*.csv` | The same statistics as flat CSV tables |
| `miniarith-v1/dedup_report.json` | Every duplicate group and its canonical representative |
| `miniarith-v1/build_info.json` | Build environment (Python, platform, gcc) and the manifest SHA-256. This is the only file that depends on the machine |
| `../benchmarks/configs/miniarith-v1.json` | The configuration the release is built from |

## Reproducing

```bash
python -m benchmarks.build            # rebuild dataset/miniarith-v1 from the config
python -m benchmarks.build --check    # rebuild in memory and compare with the committed manifest
python -m benchmarks.build --check --no-gcc   # same, without a C compiler (skips only the gcc cross-check fields)
python -m benchmarks.dedup dataset/miniarith-v1/manifest.jsonl   # re-run deduplication on a manifest
```

The build is deterministic. Each generated program is built from
`random.Random(f"{dataset_seed}:{program_id}")`, and string seeds hash the same on every
platform. Test vectors come from `random.Random(f"{dataset_seed}:tests:{program_id}")`. The
manifest contains no timestamps.

## How each group is collected

**A. Controlled.**
- The 15 Review-1 programs come from `benchmarks/dataset/review1_dataset.json` (seed 2026),
  which `benchmarks/generate_dataset.py` still reproduces byte for byte. Their stored test
  outputs are re-checked against the reference interpreter (`review1_outputs_reproduced`).
- The synthetic programs come from `benchmarks/templates.py` (template version 1.0). There
  are six categories: arithmetic identities, constant-heavy, dead-code, common-subexpression,
  strength-reduction and mixed. Two recorded knobs vary per program: `size` (small, medium or
  large) and `redundancy` (low or high). The templates also plant negative constants,
  redefinitions, CSE availability kills, dead chains and dead divisions that could trap.

**B. Randomized.** `benchmarks/randomgen.py` (version 1.0) draws statements from the grammar
with the probabilities in `RANDOM_CONFIG`, which is stored in every record. It does not target
any particular optimisation. It does exercise negative values, redefinition of inputs, shared
sub-expressions, division and modulo by variables (which can divide by zero) and occasional
division by a literal 0.

**C. External.** `benchmarks/external.py` reads `jotai_unique_300/` and checks each file's
SHA-256 against `jotai_unique_300/manifest.csv`. It translates a function only if the
function lies entirely inside a conservative C subset: an `int` function with `int`
parameters, `int` locals, the operators `+ - * / %`, assignments and one final `return`.
Anything else makes the file *incompatible*, and the record states the reason. Most of
these files use `unsigned` types (166), branches (39) or pointers. No partial or approximate
translations are produced. The translation steps and their limitations are listed in each
record's `conversion` field. In short:
- C `int` is 32 bits and signed overflow is undefined, so test vectors that leave the int32
  range are discarded.
- Division by zero is undefined in C, so it is never run through C.
- When `gcc` is available, each translation is cross-checked: the *verbatim* upstream
  function is compiled into a harness and its outputs are compared with the reference
  interpreter on every safe vector. All 12 translated functions pass on 15 vectors each.
- The builder also tries to build the complete upstream Jotai file. On the MinGW gcc used for
  the release build, these Linux-oriented files fail with a `size_t` typedef clash. Each record
  notes this, so on that platform the upstream harness cases were checked only through the
  verbatim-function harness.

The Jotai collection is kept as a **source collection**. Its 300 files are not counted as
benchmarks.

## Validation rules (applied to every candidate)

1. The program must parse and compile to TAC (otherwise `malformed`).
2. For every test vector, three executors must give identical observable behaviour (printed
   values plus error kind): the AST reference interpreter (`execution/reference.py`, which has
   its own arithmetic and is independent of the TAC generator and optimiser), the TAC
   interpreter, and the TAC-compiled-to-Python backend. Otherwise the reason is
   `frontend-mismatch` or `backend-mismatch`.
3. Reject the program if it prints nothing (`no-output`), if every test ends in a runtime
   error (`always-fails`), if any intermediate value exceeds the int64 range
   (`magnitude-exceeds-int64`), if it has more than 200 TAC instructions (`too-large`), or if
   it has fewer than 2 non-I/O instructions (`trivial`). External fragments are exempt from
   the `trivial` rule.
4. For external fragments, reject the program if the gcc cross-check failed.
5. Deduplicate. A record is a duplicate if its *normalised source* (comments and whitespace
   removed) or its *canonical TAC* (variables and temporaries renamed in order of appearance)
   equals that of an earlier record. The first record in build order is kept as the canonical
   representative. Build order is controlled, then randomized, then external, with Review-1
   first and then by id. Duplicates stay in the manifest with `status: "duplicate"` and
   `duplicate_of`. Source evidence is never deleted.

Expected outputs always come from the AST reference interpreter, never from an optimised
program. Programs whose tests include an expected runtime error are kept on purpose (126
accepted programs have at least one), because error behaviour is observable.

## Record schema (main fields)

`id`, `status` (accepted | rejected | duplicate | incompatible), `group`, `subgroup`,
`category`, `review1_member`, `source`, `source_sha256`, `normalized_source`,
`normalized_source_sha256`, `tac`, `tac_sha256`, `canonical_tac_sha256`, `n_inputs`,
`n_tac_instructions`, `opportunities` (candidate sites per operator on the naive TAC),
`origin` (generator, version, seed and config, or the upstream collection, file, SHA-256,
function and path hint), `conversion` (external only), `attribution`, `license`, `tests`
(inputs, `expected_outputs`, `expected_error`), `expected_outputs_from`,
`int32_safe_on_tests`, `validation` (checks, status, reason, message) and `duplicate_of`.

## Licensing

- **Groups A and B** were generated by this project's code. They fall under the repository's
  licence. No `LICENSE` file existed when this release was built, so the authors still need
  to choose one.
- **Group C** is derived from the Jotai benchmarks
  (<https://github.com/lac-dcc/jotai-benchmarks>). That repository declares **GPL-3.0** at
  repository level, which was checked on the GitHub repository page on 2026-10-12. Jotai took
  its functions from AnghaBench, which mined them from open-source GitHub projects. Neither
  Jotai nor AnghaBench states the originating project or licence of each function, so every
  external record has `license.status = "unverified"`. The project's own tooling licence does
  **not** apply to these functions. If you need clean licensing, exclude `group == "external"`.
- Citation for Jotai: C. C. Kind, M. Canesche, F. M. Q. Pereira. *Jotai: a Methodology for the
  Generation of Executable C Benchmarks.* Tech. Report 02-2022, UFMG, 2022.

## Limitations

- MiniArith is a straight-line integer language. Most real C code cannot be represented, so
  the external group is very small (7 programs) and its programs are simple. Results for
  group C are reported but carry little statistical weight.
- Integers are unbounded, while C uses 32-bit `int`. 393 of the 454 accepted programs stay in
  the int32 range on their tests (`int32_safe_on_tests`).
- The synthetic templates are designed to contain the opportunities the optimiser targets,
  so they favour it by construction. The randomized group is the less biased sample.
- Test vectors are finite. Expected outputs are exact for the stored vectors only.
- The 1,300-program fuzzing workload in `tests/fuzz.py` is a separate correctness workload.
  It is not part of this optimisation dataset.
