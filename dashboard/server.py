"""Local dashboard server: real compiler/optimizer calls + generated artifacts.

    python -m dashboard.server              # http://127.0.0.1:8765
    python -m dashboard.server --port 9000 --host 127.0.0.1

Standard library only (http.server). Security model:
  * binds to 127.0.0.1 by default;
  * never executes shell commands or user-supplied code: submitted text is only
    ever passed to the MiniArith lexer/parser, and the optimiser runs on the
    resulting TAC; requests are size-limited and time-limited;
  * file downloads are restricted to a whitelist of artifact directories.

API (JSON):
  GET  /api/health
  GET  /api/overview                 dataset statistics + experiment headline numbers
  GET  /api/dataset                  slim rows for every manifest record
  GET  /api/dataset/<id>             full manifest record
  GET  /api/results                  results/final/summary.json
  GET  /api/results/programs         per-program rows (results/final/per_program.csv)
  GET  /api/results/program/<id>     rows + transformation steps + timing for one program
  GET  /api/samples                  example programs for the compiler page
  POST /api/compile                  {source, inputs?, strategies?, unsafe?, weights?}
  GET  /files/<path>                 download a whitelisted artifact
"""

import argparse
import csv
import json
import mimetypes
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from execution.interpreter import run  # noqa: E402
from execution.reference import ast_run_detailed  # noqa: E402
from execution.timing import summarize, time_variants  # noqa: E402
from execution.validator import Validator  # noqa: E402
from frontend.lexer import LexError  # noqa: E402
from frontend.parser import ParseError, parse  # noqa: E402
from frontend.tac import TACGenerator, basic_blocks, format_tac, has_control_flow  # noqa: E402
from optimizer.cost import WEIGHT_CONFIGS, WEIGHTS  # noqa: E402
from optimizer.mutations import OPERATOR_NAMES, OPERATORS, UNSAFE_OPERATORS  # noqa: E402
from optimizer.search import optimize as greedy_optimize  # noqa: E402
from optimizer.strategies import STRATEGIES, StrategyResult  # noqa: E402
from frontend.tac import parse_tac  # noqa: E402

STATIC = os.path.join(ROOT, "dashboard", "static")
DATASET_DIR = os.path.join(ROOT, "dataset", "miniarith-v1")
RESULTS_DIR = os.path.join(ROOT, "results", "final")
DOWNLOAD_DIRS = {"dataset": DATASET_DIR, "results": RESULTS_DIR,
                 "plots": os.path.join(RESULTS_DIR, "plots")}

MAX_SOURCE = 6000
MAX_TAC = 160
MAX_VECTORS = 20
MAX_ABS_INPUT = 10 ** 12
STRATEGY_TIME_LIMIT = 20
COMPILE_LOCK = threading.Lock()  # one optimisation at a time keeps the machine responsive

SAMPLES = [
    {"name": "Review-1 mixed (P13)", "source": None, "dataset_id": "P13"},
    {"name": "Dead code + trapping division", "source":
        "# the dead division may fail when b == 0, so it must be kept\ninput a, b;\nx = a * 2 + 0;\n"
        "dead = x * 7;\nq = a / b;\ny = (a + b) * (a + b);\nprint x + y;\n"},
    {"name": "Common subexpressions", "source":
        "input a, b, c;\nu = (a + b) * c + (b + a);\nw = (a + b) - c * 4;\nz = (a + b) * c - w;\nprint u;\nprint z;\n"},
    {"name": "Constants + strength reduction", "source":
        "input n;\nk = 3 * 4;\nm = k - 8;\nr = n * m + n * 8 + k / 5;\nprint r * 2;\n"},
    {"name": "if/else (CFG extension)", "source":
        "input a, b;\nk = 2;\nif (k > 1) { x = a * 2; } else { x = b / a; }\n"
        "if (a < b) { print x + 0; } else { print b; }\nprint x * 1;\n"},
    {"name": "C division semantics", "source": "input a, b;\nprint a / b;\nprint a % b;\nprint -a / b;\n"},
]


class BadRequest(Exception):
    pass


# --- cached artifact loading -------------------------------------------------------
_cache = {}


def load_cached(path, loader):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    value = loader(path)
    _cache[path] = (mtime, value)
    return value


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def manifest():
    return load_cached(os.path.join(DATASET_DIR, "manifest.jsonl"), _read_jsonl) or []


def slim(r):
    tests = r.get("tests") or []
    return {
        "id": r["id"], "status": r["status"], "group": r["group"], "subgroup": r["subgroup"],
        "category": r["category"], "review1": r["review1_member"], "n_inputs": r.get("n_inputs"),
        "n_tac": r.get("n_tac_instructions"), "n_tests": len(tests),
        "error_tests": sum(t["expected_error"] is not None for t in tests),
        "source_sha256": r.get("source_sha256"), "canonical_tac_sha256": r.get("canonical_tac_sha256"),
        "seed": r["origin"].get("seed"), "origin": r["origin"].get("generator") or r["origin"].get("collection"),
        "license": r["license"]["status"], "int32_safe": r.get("int32_safe_on_tests"),
        "reason": r["validation"].get("reason"), "duplicate_of": r.get("duplicate_of"),
        "opportunities": r.get("opportunities"),
    }


# --- compile / optimise -----------------------------------------------------------
def parse_inputs(raw, n_inputs):
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        vectors = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                vectors.append([int(x) for x in line.replace(",", " ").split()])
            except ValueError:
                raise BadRequest(f"input line {line!r} is not a list of integers") from None
    elif isinstance(raw, list):
        vectors = raw
    else:
        raise BadRequest("inputs must be text or a list of integer lists")
    if len(vectors) > MAX_VECTORS:
        raise BadRequest(f"at most {MAX_VECTORS} input vectors")
    out = []
    for v in vectors:
        if not isinstance(v, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in v):
            raise BadRequest("each input vector must be a list of integers")
        if any(abs(x) > MAX_ABS_INPUT for x in v):
            raise BadRequest(f"input values must satisfy |v| <= {MAX_ABS_INPUT}")
        if len(v) != n_inputs:
            raise BadRequest(f"each input vector needs exactly {n_inputs} value(s) (got {len(v)})")
        out.append(v)
    return out


def tac_lines(program):
    return [str(i) for i in program]


def compile_request(body):
    source = body.get("source")
    if not isinstance(source, str) or not source.strip():
        raise BadRequest("source is required")
    if len(source) > MAX_SOURCE:
        raise BadRequest(f"source longer than {MAX_SOURCE} characters")
    strategies = body.get("strategies") or ["fixed", "greedy", "beam"]
    if not isinstance(strategies, list) or not set(strategies) <= {"fixed", "greedy", "beam"}:
        raise BadRequest("strategies must be a subset of fixed, greedy, beam")
    weights_name = body.get("weights") or "baseline"
    if weights_name not in WEIGHT_CONFIGS:
        raise BadRequest(f"unknown weight configuration {weights_name!r}")
    weights = WEIGHT_CONFIGS[weights_name]
    unsafe = bool(body.get("unsafe"))

    t0 = time.perf_counter()
    try:
        ast = parse(source)
        program = TACGenerator().gen_program(ast)
    except (LexError, ParseError) as exc:
        return {"ok": False, "stage": "parse", "error": str(exc)}
    if len(program) > MAX_TAC:
        raise BadRequest(f"program compiles to {len(program)} TAC instructions (limit {MAX_TAC})")
    n_in = sum(1 for i in program if i.op == "input")
    user_vectors = parse_inputs(body.get("inputs"), n_in)
    validator = Validator(program, protocol="v3")
    check_vectors = [list(v) for v in user_vectors] + [list(c) for c in validator.cases[:12]
                                                       if list(c) not in user_vectors]
    operators = OPERATORS + (UNSAFE_OPERATORS if unsafe else [])
    cf = has_control_flow(program)
    blocks = [{"index": b.index, "label": b.label, "start": b.start, "end": b.end, "successors": list(b.successors)}
              for b in basic_blocks(program)] if program else []

    results = {}
    with COMPILE_LOCK:
        for name in strategies:
            kw = dict(weights=weights, protocol="v3", time_limit=STRATEGY_TIME_LIMIT, operators=operators)
            if name == "beam":
                kw.update(width=4, patience=3, max_evaluations=20000)
            trace = None
            if name == "greedy":  # same search as STRATEGIES["greedy"], traced candidate by candidate
                trace = []
                gres = greedy_optimize(program, operators, weights=weights, protocol="v3",
                                       on_candidate=trace.append)
                res = StrategyResult("greedy", program, gres.optimized, gres.base_metrics, gres.final_metrics,
                                     gres.final_cost,
                                     [dict(op_id=s.op_id, description=s.description, cost=s.cost) for s in gres.steps],
                                     gres.candidates_total, gres.rejected_total, gres.candidates_total + 1,
                                     gres.seconds)
            else:
                res = STRATEGIES[name](program, **kw)
            entry = {
                "optimized_tac": tac_lines(res.optimized),
                "steps": res.steps, "final_cost": res.final_cost, "status": res.status,
                "metrics_before": res.base_metrics.as_dict(), "metrics_after": res.final_metrics.as_dict(),
                "candidates_evaluated": res.candidates_evaluated, "candidates_rejected": res.candidates_rejected,
                "search_seconds": round(res.seconds, 4),
            }
            if trace is not None:
                rejected = [{"iteration": c["iteration"], "op_id": c["mutation"].op_id,
                             "description": c["mutation"].description} for c in trace if not c["valid"]]
                entry["rejected_candidates"] = rejected[:100]
                entry["rejected_by_op"] = dict(gres.rejected_by_op)
                per_iter = {}
                for c in trace:
                    it = per_iter.setdefault(c["iteration"], {"iteration": c["iteration"], "evaluated": 0,
                                                              "valid": 0, "improving": 0})
                    it["evaluated"] += 1
                    it["valid"] += c["valid"]
                    it["improving"] += c["improves"]
                entry["iterations"] = list(per_iter.values())
            checks = []
            for v in check_vectors:
                ref = ast_run_detailed(ast, v)
                orig = run(program, v)
                opt = run(res.optimized, v)
                checks.append({"inputs": v, "user": v in user_vectors,
                               "reference": {"outputs": list(ref.outputs), "error": ref.error},
                               "original": {"outputs": list(orig.outputs), "error": orig.error},
                               "optimized": {"outputs": list(opt.outputs), "error": opt.error},
                               "match": opt.observable == ref.observable and orig.observable == ref.observable})
            entry["checks"] = checks
            results[name] = entry

        timing = None
        batch = [tuple(v) for v in check_vectors if ast_run_detailed(ast, v).error is None] or \
                [tuple(v) for v in check_vectors]
        if batch:
            variants = {"unoptimized": program}
            for name in strategies:
                variants[name] = parse_tac("\n".join(results[name]["optimized_tac"]))
            samples = time_variants(variants, batch, backend="interp", warmup=2, repeats=9)
            timing = {"backend": "TAC interpreter (Python)", "batch_size": len(batch), "repeats": 9,
                      "per_strategy": {k: summarize(v, per=len(batch)) for k, v in samples.items()}}

    return {
        "ok": True, "control_flow": cf, "n_inputs": n_in,
        "original_tac": tac_lines(program), "blocks": blocks,
        "weights": {"name": weights_name, "values": weights},
        "validation_vectors": len(validator.cases), "results": results, "timing": timing,
        "operator_names": OPERATOR_NAMES, "seconds": round(time.perf_counter() - t0, 3),
        "notes": ["Simulated cycles come from the deterministic cycle model, not CPU cycles.",
                  "Measured time is the Python TAC interpreter (median of 9 interleaved runs); "
                  "small differences are within noise.",
                  "Correctness = identical printed values and error kind vs. the AST reference interpreter "
                  "on the listed inputs (testing, not proof)."],
    }


# --- HTTP layer --------------------------------------------------------------------
def overview():
    stats = load_cached(os.path.join(DATASET_DIR, "statistics.json"), _read_json)
    summary = load_cached(os.path.join(RESULTS_DIR, "summary.json"), _read_json)
    info = load_cached(os.path.join(DATASET_DIR, "build_info.json"), _read_json)
    plots = sorted(f for f in os.listdir(os.path.join(RESULTS_DIR, "plots"))
                   if f.endswith(".png")) if os.path.isdir(os.path.join(RESULTS_DIR, "plots")) else []
    head = None
    if summary:
        head = {k: summary.get(k) for k in ("dataset", "config", "environment", "seconds", "correctness",
                                            "overall", "by_group", "comparisons", "timing", "sensitivity")}
        head["results_mtime"] = os.path.getmtime(os.path.join(RESULTS_DIR, "summary.json"))
    validator_eval = load_cached(os.path.join(RESULTS_DIR, "validator_eval.json"), _read_json)
    verification = load_cached(os.path.join(RESULTS_DIR, "verification_summary.json"), _read_json)
    return {"dataset_statistics": stats, "dataset_build": info, "experiments": head, "plots": plots,
            "validator_eval": _validator_view(validator_eval),
            "verification": verification, "operator_names": OPERATOR_NAMES, "cost_weights": WEIGHTS}


def _validator_view(ve):
    """Slim view of results/final/validator_eval.json (no per-miss lists)."""
    if not ve:
        return None
    evals = ve.get("evaluations") or {f"{k}|oracle300": ve[k] for k in ("review1", "v2") if k in ve}
    slim_evals = {k: {f: v[f] for f in ("protocol", "oracle", "mutants", "totals", "detection_rate_on_nonequivalent")
                      if f in v} for k, v in evals.items()}
    head = slim_evals.get("v3|oracle_strong") or slim_evals.get("v2|oracle300")
    return {"evaluations": slim_evals, "headline": head, "blind_spot_demo": ve.get("blind_spot_demo")}


def program_detail(pid):
    rows = [r for r in (load_cached(os.path.join(RESULTS_DIR, "per_program.csv"), _read_csv) or [])
            if r["program_id"] == pid]
    steps = [s for s in (load_cached(os.path.join(RESULTS_DIR, "steps.jsonl"), _read_jsonl) or [])
             if s["program_id"] == pid]
    timing = [t for t in (load_cached(os.path.join(RESULTS_DIR, "timing.csv"), _read_csv) or [])
              if t["program_id"] == pid]
    rec = next((r for r in manifest() if r["id"] == pid), None)
    return {"rows": rows, "steps": steps, "timing": timing,
            "source": rec.get("source") if rec else None, "tac": rec.get("tac") if rec else None}


class Handler(BaseHTTPRequestHandler):
    server_version = "MiniArithDashboard/1.0"

    def log_message(self, fmt, *args):
        if self.server.verbose:
            super().log_message(fmt, *args)

    def send_json(self, obj, status=200):
        data = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path, download=False):
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith(".jsonl"):
            ctype = "application/x-ndjson"
        with open(path, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
        self.end_headers()
        self.wfile.write(data)

    def safe_join(self, base, rel):
        path = os.path.realpath(os.path.join(base, rel))
        if not path.startswith(os.path.realpath(base) + os.sep) or not os.path.isfile(path):
            return None
        return path

    def do_GET(self):
        url = urlparse(self.path)
        p = unquote(url.path)
        try:
            if p == "/api/health":
                return self.send_json({"ok": True, "python": sys.version.split()[0]})
            if p == "/api/overview":
                return self.send_json(overview())
            if p == "/api/dataset":
                return self.send_json({"records": [slim(r) for r in manifest()]})
            if p.startswith("/api/dataset/"):
                rid = p.rsplit("/", 1)[1]
                rec = next((r for r in manifest() if r["id"] == rid), None)
                return self.send_json(rec) if rec else self.send_json({"error": "no such record"}, 404)
            if p == "/api/results":
                s = load_cached(os.path.join(RESULTS_DIR, "summary.json"), _read_json)
                return self.send_json(s) if s else self.send_json({"error": "no results yet: run "
                                                                   "python -m experiments.run_experiments"}, 404)
            if p == "/api/results/programs":
                return self.send_json({"rows": load_cached(os.path.join(RESULTS_DIR, "per_program.csv"),
                                                           _read_csv) or []})
            if p.startswith("/api/results/program/"):
                return self.send_json(program_detail(p.rsplit("/", 1)[1]))
            if p == "/api/samples":
                out = []
                for s in SAMPLES:
                    src = s["source"]
                    if src is None:
                        rec = next((r for r in manifest() if r["id"] == s["dataset_id"]), None)
                        src = rec["source"] if rec else "input a;\nprint a;\n"
                    out.append({"name": s["name"], "source": src})
                return self.send_json({"samples": out, "weights": list(WEIGHT_CONFIGS)})
            if p.startswith("/files/"):
                parts = p[len("/files/"):].split("/", 1)
                if len(parts) == 2 and parts[0] in DOWNLOAD_DIRS:
                    path = self.safe_join(DOWNLOAD_DIRS[parts[0]], parts[1])
                    if path:
                        return self.send_file(path, download=not path.endswith(".png"))
                return self.send_json({"error": "not found"}, 404)
            rel = "index.html" if p in ("/", "") else p.lstrip("/")
            path = self.safe_join(STATIC, rel)
            if path:
                return self.send_file(path)
            return self.send_json({"error": "not found"}, 404)
        except Exception as exc:  # pragma: no cover - reported to client
            traceback.print_exc()
            return self.send_json({"error": f"internal error: {type(exc).__name__}"}, 500)

    def do_POST(self):
        if urlparse(self.path).path != "/api/compile":
            return self.send_json({"error": "not found"}, 404)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 64 * 1024:
            return self.send_json({"error": "request body missing or too large"}, 413)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise BadRequest("JSON object expected")
            return self.send_json(compile_request(body))
        except (BadRequest, json.JSONDecodeError, UnicodeDecodeError) as exc:
            return self.send_json({"ok": False, "stage": "request", "error": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover
            traceback.print_exc()
            return self.send_json({"ok": False, "stage": "internal", "error": f"{type(exc).__name__}: {exc}"}, 500)


def make_server(host="127.0.0.1", port=8765, verbose=False):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.verbose = verbose
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description="MiniArith optimizer dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    srv = make_server(args.host, args.port, args.verbose)
    print(f"dashboard: http://{args.host}:{args.port}/   (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
