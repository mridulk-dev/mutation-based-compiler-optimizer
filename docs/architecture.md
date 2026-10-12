# System architecture

The system is a search-based optimiser for three-address code (TAC). It takes a MiniArith
program, compiles it to deliberately naive TAC, and improves that TAC with **mutation
operators** (rewrite rules). Each operator only *proposes* a change. A **validator** rejects
any candidate whose observable behaviour differs from the original program, where observable
behaviour means the printed values and the runtime error, if any. A **search strategy** then
keeps the cheapest valid program under a weighted **cost model**.

## Components

```mermaid
flowchart TB
    subgraph FE["Compiler frontend (frontend/)"]
        SRC["MiniArith source (.src)"] --> LEX["lexer.py<br/>tokens"] --> PAR["parser.py<br/>AST + definite assignment"]
        PAR --> GEN["tac.py: TACGenerator<br/>naive TAC"]
        GEN --> CFG["tac.py: basic_blocks()<br/>CFG (if/else extension)"]
    end
    subgraph OPT["Optimizer (optimizer/)"]
        MUT["mutations.py<br/>M1–M9 local, per basic block<br/>M10 branch folding (CFG)"]
        EVAL["strategies.py: Evaluator<br/>memoised validate + cost"]
        COST["cost.py<br/>weighted, normalised cost<br/>WEIGHT_CONFIGS"]
        STRAT["Strategies<br/>fixed · greedy (search.py) · beam"]
    end
    subgraph EXE["Execution (execution/)"]
        VAL["validator.py<br/>differential testing<br/>protocols review1 · v2 · v3"]
        INT["interpreter.py<br/>TAC interpreter + cycle model"]
        PYC["pycodegen.py<br/>TAC → Python function"]
        REF["reference.py<br/>AST interpreter<br/>(independent arithmetic)"]
        TIM["timing.py<br/>wall-clock harness"]
    end
    subgraph DATA["Dataset (benchmarks/ → dataset/)"]
        TPL["templates.py · randomgen.py<br/>external.py (Jotai C subset)"]
        BLD["build.py · dedup.py · stats.py"]
        MAN[("manifest.jsonl<br/>statistics · dedup report")]
    end
    subgraph EXP["Experiments (experiments/)"]
        RUN["run_experiments.py<br/>parallel search + checks<br/>sequential timing"]
        VE["validator_eval.py"]
        PLT["plots.py · summarize.py · report.py"]
        RES[("results/final/<br/>CSV · JSONL · JSON · PNG")]
    end
    DASH["dashboard/server.py + static/<br/>local web UI"]

    CFG --> STRAT
    STRAT --> MUT --> EVAL
    EVAL --> VAL --> INT
    EVAL --> COST
    TPL --> BLD --> MAN
    REF --> BLD
    INT --> BLD
    PYC --> BLD
    MAN --> RUN --> STRAT
    RUN --> REF
    RUN --> PYC
    RUN --> TIM
    RUN --> RES
    VE --> RES
    RES --> PLT --> RES
    MAN --> DASH
    RES --> DASH
    DASH -- "POST /api/compile" --> PAR
```

| Module | Responsibility |
|---|---|
| `frontend/lexer.py` | Tokenises MiniArith and rejects `t<digits>` names, which are reserved for temporaries. |
| `frontend/parser.py` | Recursive-descent parser that produces an AST. It enforces definite assignment, so an accepted program can never read an undefined variable, including across `if/else`. |
| `frontend/tac.py` | TAC IR (`Instr`). It holds the naive generator (one temporary per operator, one copy per assignment), the TAC text parser, basic blocks, the CFG, and canonical (alpha-renamed) TAC for deduplication. |
| `execution/interpreter.py` | Executes TAC and counts **simulated cycles**. An undefined variable or jump target is reported as a runtime error, never a crash. |
| `execution/reference.py` | The semantic oracle. It evaluates the AST directly, with its own C-style arithmetic, and imports nothing from the TAC side. This independence is checked by a unit test. |
| `execution/pycodegen.py` | Compiles straight-line TAC to a Python function. It serves as a second executor for checks and as the second timing backend. |
| `execution/validator.py` | Differential testing against the original program. Three protocols: `review1` (25 seeded vectors, the default), `v2` (+ structured vectors) and `v3` (+ coverage vectors; used by the final experiments and the dashboard). |
| `execution/timing.py` | Wall-clock timing: warm-up runs, interleaved rotating order, GC disabled, and raw samples returned. |
| `optimizer/mutations.py` | Operators M1–M10 and the unsafe demo operator MX. `generate_mutations` applies local operators one basic block at a time. |
| `optimizer/cost.py` | Metrics and the weighted cost. The baseline weights are those of Review-1. |
| `optimizer/search.py` | Greedy best-improvement search (Review-1, unchanged apart from optional parameters). |
| `optimizer/strategies.py` | Shared `Evaluator`, plus the fixed-order pipeline, greedy wrapper and beam search. |
| `benchmarks/*` | Dataset generators, extraction, build, deduplication and statistics (see `dataset/README.md`). |
| `experiments/*` | Experiment runner, aggregation, validator self-evaluation, plots and report generation. |
| `dashboard/*` | Local web dashboard built on the standard library HTTP server, with vanilla JS and no CDN. |
| `reproduce.py` | One command for the whole verification and experiment pipeline. |

## End-to-end workflow

```mermaid
flowchart LR
    C["config<br/>miniarith-v1.json"] --> B["benchmarks.build<br/>generate · validate · dedup"]
    B --> M[("dataset manifest")]
    M --> X["experiments.run_experiments"]
    subgraph X1["per program (parallel workers)"]
        direction TB
        STR["strategies: unoptimized · fixed · greedy · beam · greedy w/o M8"]
        CK["independent checks<br/>fixtures + 200 held-out inputs<br/>AST reference + compiled backend"]
        SE["sensitivity: 6 weight configs × (fixed, greedy)"]
        STR --> CK
        STR --> SE
    end
    X --> X1 --> T["timing phase<br/>sequential, interleaved"]
    T --> R[("per_program.csv · steps.jsonl<br/>sensitivity.csv · timing(_raw).csv<br/>summary.json")]
    M --> V["experiments.validator_eval"] --> R
    R --> P["experiments.plots"] --> R
    R --> D["docs/results.md<br/>(experiments.report)"]
    R --> W["dashboard"]
```

## Search loop (greedy; the other strategies differ only in step 4)

```mermaid
flowchart TD
    S(["naive TAC"]) --> SET["validator: run original on test vectors<br/>baseline metrics → cost 1.0"]
    SET --> GENM["generate every single-site mutation<br/>(per basic block for if/else programs)"]
    GENM --> DEDUP["drop duplicates / unchanged"]
    DEDUP --> V{"same outputs + error kind<br/>on every vector?"}
    V -- no --> REJ["reject (logged)"]
    V -- yes --> SC["measure + weighted cost"]
    SC --> PICK{"strictly cheaper than current?"}
    PICK -- "yes (best of iteration)" --> ACC["accept → current"] --> GENM
    PICK -- "none" --> OUT(["optimised TAC"])
```

- **fixed:** applies the first valid candidate of each operator in `FIXED_ORDER`,
  regardless of cost, until a fixpoint is reached. It then runs an M8 scheduling pass that
  accepts only swaps that lower simulated cycles.
- **greedy:** at each step, picks the best strictly improving candidate.
- **beam:** keeps the `width` cheapest distinct valid programs at each depth, and stops after
  `patience` depths without a new best program.

## Control-flow extension (staged)

`if (cond) { … } else { … }` compiles to `ifz c goto L`, `goto L` and `label L`.
`basic_blocks()` splits the program at labels and after jumps, and links the blocks into a
CFG. Every local operator sees **one block body plus its terminating jump**. It never sees a
label. It also receives a conservative `live_out` set: every variable read anywhere outside
that slice. if/else code is acyclic, so this set over-approximates true liveness, and
dead-code decisions stay sound. M8 never moves code across a label or jump. M10 folds
`ifz <constant>`, and in the same step removes unreachable blocks, redundant jumps and
unused labels. Straight-line programs form a single block with an empty `live_out`, which
is exactly the Review-1 code path. A test confirms that Review-1 results reproduce byte for
byte, apart from timing fields.

**Status:** implemented and tested (exhaustive operator soundness on control-flow programs,
property tests and fuzzing). It is **not** part of the benchmark dataset, which is
straight-line only. There are no loops, so no cyclic CFG or iterative data-flow analysis.
