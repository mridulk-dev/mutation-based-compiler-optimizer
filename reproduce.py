"""End-to-end reproducibility pipeline.

    python reproduce.py                 # full: verify dataset, tests, fuzzing, full experiments, plots, checks
    python reproduce.py --quick         # CI-sized: same steps, small fuzz + 30-program experiment subset
    python reproduce.py --skip-experiments   # verify everything against the existing results/final

Steps (each recorded with status, duration and detail):
  1  environment      Python version, required modules, config files, optional gcc
  2  dataset          rebuild dataset in memory and compare with the committed manifest;
                      Review-1 generator still reproduces the committed 15-program file;
                      independent audit (benchmarks.audit) -> dataset_audit.json
  3  dedup            re-run deduplication on the committed manifest == committed dedup_report.json
  4  unit tests       python -m unittest discover -s tests -t .
  5  fuzzing          straight-line + control-flow random programs (separate correctness workload)
  6  experiments      baselines/search/sensitivity/timing (experiments.run_experiments) +
                      validator self-evaluation (experiments.validator_eval)
  7  plots            experiments.plots from the saved logs
  8  dashboard        start the dashboard server in-process and check that its API returns
                      exactly the numbers in the artifact files
  9  summary          results/<out>/verification_summary.json

Exit code 0 only if every step passed (skipped steps do not fail the run).
Before a full run overwrites results/final, the previous summary.json is archived in
results/history/ so earlier results keep their provenance.
"""

import argparse
import hashlib
import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


class Step:
    def __init__(self, name):
        self.name, self.status, self.detail, self.seconds = name, "pending", "", 0.0


def sh(args, timeout=None):
    """Run a Python module/script of this repository (never a shell string)."""
    proc = subprocess.run([PY, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode, tail


def rel(path):
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:  # different drive on Windows
        return path


def git_info():
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
        return {"commit": commit or None, "dirty_worktree": dirty}
    except OSError:
        return {"commit": None, "dirty_worktree": None}


def step_environment(args):
    ok = sys.version_info >= (3, 10)
    missing = []
    for mod in ("matplotlib",):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    files = ["benchmarks/configs/miniarith-v1.json", "benchmarks/dataset/review1_dataset.json",
             "dataset/miniarith-v1/manifest.jsonl", "jotai_unique_300/manifest.csv"]
    absent = [f for f in files if not os.path.exists(os.path.join(ROOT, f))]
    gcc = shutil.which("gcc")
    detail = (f"Python {platform.python_version()}; gcc {'found' if gcc else 'not found (external cross-check skipped)'}"
              + (f"; missing modules: {missing}" if missing else "") + (f"; missing files: {absent}" if absent else ""))
    return ok and not absent and not missing, detail


def step_dataset(args):
    flags = ["--check"] + ([] if shutil.which("gcc") else ["--no-gcc"])
    code, out = sh(["-m", "benchmarks.build", *flags], timeout=1800)
    if code:
        return False, " | ".join(out[-3:])
    sys.path.insert(0, ROOT)
    from benchmarks.generate_dataset import DATASET_PATH, generate
    with open(DATASET_PATH, encoding="utf-8") as fh:
        review1_ok = json.load(fh) == generate(2026)
    acode, aout = sh(["-m", "benchmarks.audit", "--json", os.path.join(args.out, "dataset_audit.json")], timeout=1800)
    audit = next((ln for ln in aout if ln.startswith("{")), "")
    return review1_ok and acode == 0, (out[-1] + f"; Review-1 file reproduces: {review1_ok}; independent audit "
                                       f"{'passed' if acode == 0 else 'FAILED'} {audit}")


def step_dedup(args):
    from benchmarks.dedup import deduplicate
    path = os.path.join(ROOT, "dataset", "miniarith-v1", "manifest.jsonl")
    with open(path, encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh]
    for r in records:
        if r["status"] == "duplicate":
            r["status"] = "accepted"
    report = deduplicate(records)
    with open(os.path.join(ROOT, "dataset", "miniarith-v1", "dedup_report.json"), encoding="utf-8") as fh:
        committed = json.load(fh)
    same = report == committed
    return same, f"{report['n_duplicate_records']} duplicate records in {len(report['duplicate_groups'])} groups; " \
                 f"matches committed report: {same}"


def step_tests(args):
    code, out = sh(["-m", "unittest", "discover", "-s", "tests", "-t", "."], timeout=3600)
    summary = [ln for ln in out if ln.startswith("Ran ") or ln.startswith("OK") or ln.startswith("FAILED")]
    return code == 0, " ".join(summary) or " | ".join(out[-3:])


def step_fuzz(args):
    runs = [("straight-line", ["-m", "tests.fuzz", str(args.fuzz_n), "77", "--strategies", "greedy"]),
            ("straight-line fixed+beam", ["-m", "tests.fuzz", str(max(20, args.fuzz_n // 10)), "78",
                                          "--strategies", "fixed,beam"]),
            ("control-flow", ["-m", "tests.fuzz", str(max(20, args.fuzz_n // 5)), "11", "--cf",
                              "--strategies", "greedy,fixed"])]
    details, ok = [], True
    for label, cmd in runs:
        code, out = sh(cmd, timeout=7200)
        ok &= code == 0
        nums = [ln.split(":")[-1].strip() for ln in out if "programs where" in ln or "disagrees" in ln]
        details.append(f"{label}: {out[0] if out else ''} -> unsound {nums[0] if nums else '?'}, wrong {nums[1] if len(nums) > 1 else '?'}")
    return ok, "; ".join(details)


def step_experiments(args):
    out_dir = args.out
    if not args.quick and os.path.exists(os.path.join(out_dir, "summary.json")):
        hist = os.path.join(ROOT, "results", "history")
        os.makedirs(hist, exist_ok=True)
        with open(os.path.join(out_dir, "summary.json"), "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()[:12]
        shutil.copy2(os.path.join(out_dir, "summary.json"), os.path.join(hist, f"summary_{digest}.json"))
    cmd = ["-m", "experiments.run_experiments", "--out", out_dir]
    if args.quick:
        cmd += ["--limit", "30", "--repeats", "5"]
    code, out = sh(cmd, timeout=6 * 3600)
    if code:
        return False, " | ".join(out[-4:])
    vcmd = ["-m", "experiments.validator_eval", "--out", os.path.join(out_dir, "validator_eval.json")]
    if args.quick:
        vcmd += ["--limit", "30"]
    vcode, vout = sh(vcmd, timeout=3 * 3600)
    corr = next((ln.strip() for ln in out if "correctness:" in ln), "")
    return vcode == 0, f"{corr}; validator: {vout[1] if len(vout) > 1 else vout}"


def step_plots(args):
    code, out = sh(["-m", "experiments.plots", args.out], timeout=1800)
    return code == 0, f"{sum('plot:' in ln for ln in out)} plots written" if code == 0 else " | ".join(out[-3:])


def step_dashboard(args):
    sys.path.insert(0, ROOT)
    from dashboard import server as dash
    if os.path.realpath(args.out) != os.path.realpath(dash.RESULTS_DIR):
        dash.RESULTS_DIR = args.out
    srv = dash.make_server("127.0.0.1", 0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    def get(path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))

    problems = []
    try:
        with open(os.path.join(ROOT, "dataset", "miniarith-v1", "statistics.json"), encoding="utf-8") as fh:
            stats = json.load(fh)
        with open(os.path.join(args.out, "summary.json"), encoding="utf-8") as fh:
            summary = json.load(fh)
        ov = get("/api/overview")
        if ov["dataset_statistics"] != stats:
            problems.append("overview dataset statistics differ from statistics.json")
        if get("/api/results") != json.loads(json.dumps(summary)):
            problems.append("/api/results differs from summary.json")
        ds = get("/api/dataset")["records"]
        if sum(r["status"] == "accepted" for r in ds) != stats["totals"]["accepted"]:
            problems.append("dataset API accepted count differs")
        rows = get("/api/results/programs")["rows"]
        if len(rows) != summary["correctness"]["rows"]:
            problems.append(f"per-program rows {len(rows)} != summary rows {summary['correctness']['rows']}")
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/compile", method="POST",
                                     data=json.dumps({"source": "input a; x = a * 2 + 0; print x;", "inputs": "3\n-4"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            comp = json.loads(r.read().decode("utf-8"))
        if not comp.get("ok") or not all(c["match"] for res in comp["results"].values() for c in res["checks"]):
            problems.append("live compile endpoint failed")
        bad = urllib.request.Request(f"http://127.0.0.1:{port}/api/compile", method="POST",
                                     data=json.dumps({"source": "x = ;"}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(bad, timeout=60) as r:
            if json.loads(r.read().decode("utf-8")).get("ok") is not False:
                problems.append("malformed program not reported as an error")
    finally:
        srv.shutdown()
    return not problems, "API numbers match artifact files; live compile OK" if not problems else "; ".join(problems)


STEPS = [("environment", step_environment), ("dataset", step_dataset), ("dedup", step_dedup),
         ("unit tests", step_tests), ("fuzzing", step_fuzz), ("experiments", step_experiments),
         ("plots", step_plots), ("dashboard/data agreement", step_dashboard)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true", help="CI-sized run (writes to results/quick)")
    ap.add_argument("--skip-experiments", action="store_true", help="reuse existing results; skip steps 6-7")
    ap.add_argument("--skip-fuzz", action="store_true")
    ap.add_argument("--out", default=None, help="results directory (default results/final, or results/quick)")
    args = ap.parse_args(argv)
    args.out = os.path.abspath(args.out or os.path.join(ROOT, "results", "quick" if args.quick else "final"))
    args.fuzz_n = 150 if args.quick else 1300
    if args.quick and args.skip_experiments:
        ap.error("--quick and --skip-experiments are mutually exclusive")

    os.makedirs(args.out, exist_ok=True)
    started = time.time()
    steps = []
    for name, fn in STEPS:
        st = Step(name)
        steps.append(st)
        skip = (name in ("experiments", "plots") and args.skip_experiments) or (name == "fuzzing" and args.skip_fuzz)
        if skip:
            st.status, st.detail = "skipped", "requested on the command line"
            print(f"[skip] {name}")
            continue
        print(f"[....] {name}", flush=True)
        t0 = time.perf_counter()
        try:
            ok, detail = fn(args)
        except Exception as exc:  # recorded, never hidden
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        st.seconds = time.perf_counter() - t0
        st.status, st.detail = ("passed" if ok else "failed"), detail
        print(f"[{'ok' if ok else 'FAIL':>4}] {name} ({st.seconds:.0f}s): {detail}", flush=True)

    passed = all(s.status in ("passed", "skipped") for s in steps)
    summary = {
        "passed": passed, "mode": "quick" if args.quick else "full", "results_dir": rel(args.out),
        "started_unix": round(started), "seconds": round(time.time() - started, 1),
        "python": platform.python_version(), "platform": platform.platform(), "gcc": shutil.which("gcc") is not None,
        **git_info(),
        "steps": [{"name": s.name, "status": s.status, "seconds": round(s.seconds, 1), "detail": s.detail} for s in steps],
        "note": "Timing measurements and *_seconds fields are the only nondeterministic outputs.",
    }
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "verification_summary.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    print(f"\n{'ALL STEPS PASSED' if passed else 'SOME STEPS FAILED'} - "
          f"{rel(os.path.join(args.out, 'verification_summary.json'))}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
