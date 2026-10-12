# Review / demo guide

The demo takes about 10–12 minutes. Every command runs from the repository root.

## 0. Before the review (one-time, about 1 minute)

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -t .        # 120 tests, 1–3 minutes; expect "OK"
python -m benchmarks.build --check               # "OK: rebuilt dataset is identical ..."
python -m dashboard.server                       # leave running; open http://127.0.0.1:8765
```

The committed `results/final/` already holds the full experiment logs, so the dashboard shows
real results immediately. To regenerate everything, run `python reproduce.py`, which takes
about 47 minutes. Run it beforehand, not during the review.

## 1. Overview page (1 minute)

- Hero number: **accepted benchmark programs**, counted by the validator rather than by a
  generation target.
- Tiles: correct results, mean cost reduction (marked *simulated*), and measured speed-up
  (marked *measured*).
- Dataset composition by group. Groups are always reported separately.
- Head-to-head table: fixed vs greedy are nearly tied, and beam is slightly better on many
  programs.
- Plot gallery generated from the saved logs.

## 2. Interactive compiler (4 minutes)

1. **Compiler** → example *Review-1 mixed (P13)* → **Compile & optimize**.
   - Original and optimised TAC side by side. Shading marks removed and new lines.
   - Transformation trace: M2 → M9 → M3 → M7 → … → M8. Each step shows the cost after it.
   - Before/after table: instructions, arithmetic ops, temporaries, simulated cycles, and
     measured median time per strategy.
   - Correctness table: every vector agrees with the AST reference.
2. Example *Dead code + trapping division*, then enter the inputs `3 4` and `5 0`.
   - `q = a / b` is dead but **not removed**: with `b = 0` the program must still fail
     with `division by zero`. The row for `5 0` shows the same error for reference, original
     and optimised.
3. Tick **Add the unsafe operator MX**, then run again.
   - The *Rejected by validator* tile is now non-zero, and the rejected candidates are
     listed. The final program is still correct.
4. Example *if/else (CFG extension)*.
   - Callout: the number of basic blocks. M10 folds the constant `k > 1` branch and removes
     the dead `else` branch.
5. Optional: type an invalid program such as `print b;` to show the parse error message.

## 3. Dataset explorer (2 minutes)

- Status filter **accepted** gives 454 records. Filter group = *C · External*, status = *All*
  to see the 300 Jotai records: 7 accepted, 5 duplicates, and 288 incompatible with a reason
  (search for `unsigned`).
- Click **P13**. Its Review-1 badge appears; the source and tests are unchanged.
- Click **EXT-JOTAI-158**. It shows the upstream C function verbatim, the conversion steps,
  the gcc cross-check, and the licence status *unverified*. The tooling licence does not
  apply to it.
- Click a `RND-…` record and show the seed and generator config, which make it reproducible.
- Download links for the manifest and statistics.

## 4. Experiments (3 minutes)

- Strategy summary table. Show that every row is **454/454 correct**.
- *By dataset group* selector: switch to *B · Randomized*.
- Per-program comparisons: regressions are listed by id. Click one to open the drill-down.
- Cost-weight sensitivity: the **cycles_only** row is where greedy falls behind the fixed
  pipeline. Explain cost-neutral enabling moves.
- Measured wall-clock time: geomean speed-up and improved / inconclusive / regressed counts.
  The *reordering check* shows that M8's simulated gain is not visible on the interpreter.

## 5. Methodology page (1 minute)

Pipeline, metric definitions (simulated vs measured), correctness protocol (with the explicit
"not a proof" box), and limitations.

## Command-line fallback (if the browser is unavailable)

```bash
python main.py benchmarks/b5_polynomial.src           # trace + side-by-side TAC + metrics table
python main.py --unsafe --quiet                       # validator rejections
python -m tests.fuzz 200 77                           # 0 unsound / 0 wrong
python -m experiments.run_experiments --limit 20 --out results/smoke --skip-timing
```
