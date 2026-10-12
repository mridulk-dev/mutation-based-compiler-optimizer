# Superseded run (code commit ede46c9, protocol v3)

All 8 reproduce.py stages passed, but this run is **superseded** and must not be cited:

- Two beam searches (SYN-DEA-006, SYN-DEA-012) hit the 120 s wall-clock limit after ~1,300
  evaluations, because the machine slowed down (sleep/throttling; the unit-test stage also took
  1,762 s instead of ~90 s). They returned partial programs (cost 0.90 / 0.87 instead of
  0.17 / 0.25), so beam's results depended on machine speed.
- Fix (next commit): no wall-clock cutoff in experiments; only deterministic budgets.
- `dirty_worktree: true` here was caused by the pipeline overwriting its own tracked result
  files before git status was read; the non-results tree was identical to ede46c9. Fixed by
  capturing provenance at the start of a run.
