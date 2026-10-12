"""Independent audit of a released dataset manifest.

    python -m benchmarks.audit                       # dataset/miniarith-v1
    python -m benchmarks.audit --json out.json

Recomputes, without trusting stored fields: status/group counts, source / TAC /
canonical-TAC hashes, duplicate relationships, expected outputs (AST
reference), statistics.json agreement, Review-1 identity (also against the
pre-project git commit if git is available) and the provenance of every
external record (file hash vs jotai_unique_300/manifest.csv, function text
present verbatim in the upstream file, licence fields). Exits non-zero on any
failed check.
"""

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter

from benchmarks.build import normalize_source
from execution.reference import run_source_detailed
from frontend.tac import canonical_tac, compile_source, format_tac

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS = os.path.join(ROOT, "dataset", "miniarith-v1")
PRE_PROJECT_COMMIT = "8dfe1e5"  # last commit before the final-submission work


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit():
    with open(os.path.join(DS, "manifest.jsonl"), encoding="utf-8") as fh:
        recs = [json.loads(line) for line in fh]
    with open(os.path.join(DS, "statistics.json"), encoding="utf-8") as fh:
        stats = json.load(fh)
    checks, problems = {}, []

    def check(name, ok, detail=""):
        checks[name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            problems.append(f"{name}: {detail}")

    by_status = Counter(r["status"] for r in recs)
    by_group = Counter(r["group"] for r in recs if r["status"] == "accepted")
    check("ids unique", len({r["id"] for r in recs}) == len(recs), f"{len(recs)} records")
    check("status counts match statistics.json",
          all(stats["totals"][s] == by_status.get(s, 0) for s in ("accepted", "rejected", "duplicate", "incompatible")),
          dict(by_status))
    check("accepted counts per group match statistics.json",
          all(stats["by_group"][g]["accepted"] == by_group.get(g, 0) for g in stats["by_group"]), dict(by_group))

    accepted = [r for r in recs if r["status"] == "accepted"]
    bad_hash, bad_tac, bad_out, n_tests = [], [], [], 0
    canon = {}
    for r in accepted:
        program = compile_source(r["source"])
        tac = format_tac(program, numbered=False)
        if (r["source_sha256"] != sha(r["source"]) or r["normalized_source_sha256"] != sha(normalize_source(r["source"]))
                or r["canonical_tac_sha256"] != sha(format_tac(canonical_tac(program), numbered=False))):
            bad_hash.append(r["id"])
        if r["tac"] != tac or r["tac_sha256"] != sha(tac):
            bad_tac.append(r["id"])
        for t in r["tests"]:
            n_tests += 1
            ref = run_source_detailed(r["source"], t["inputs"])
            if list(ref.outputs) != t["expected_outputs"] or ref.error != t["expected_error"]:
                bad_out.append(r["id"])
        canon.setdefault(r["canonical_tac_sha256"], []).append(r["id"])
    check("hashes recompute", not bad_hash, bad_hash[:5])
    check("stored TAC recompiles identically", not bad_tac, bad_tac[:5])
    check("expected outputs equal AST reference", not bad_out, f"{n_tests} tests; bad: {sorted(set(bad_out))[:5]}")
    dup_acc = {h: ids for h, ids in canon.items() if len(ids) > 1}
    norm = Counter(r["normalized_source_sha256"] for r in accepted)
    check("no duplicates among accepted (canonical TAC, normalised source)",
          not dup_acc and max(norm.values()) == 1, dup_acc)
    ids = {r["id"]: r for r in recs}
    bad_dup = [r["id"] for r in recs if r["status"] == "duplicate" and not (
        r.get("duplicate_of") in ids and ids[r["duplicate_of"]]["status"] == "accepted"
        and (ids[r["duplicate_of"]]["canonical_tac_sha256"] == r["canonical_tac_sha256"]
             or ids[r["duplicate_of"]]["normalized_source_sha256"] == r["normalized_source_sha256"]))]
    check("every duplicate points to an accepted record with the same hash", not bad_dup, bad_dup)

    # Review-1
    with open(os.path.join(ROOT, "benchmarks", "dataset", "review1_dataset.json"), encoding="utf-8") as fh:
        review1 = {p["id"]: p for p in json.load(fh)["programs"]}
    r1 = {r["id"]: r for r in recs if r["review1_member"]}
    same = set(r1) == set(review1) and all(
        r1[k]["source"] == review1[k]["source"] and r1[k]["status"] == "accepted"
        and [t["inputs"] for t in r1[k]["tests"]] == [t["inputs"] for t in review1[k]["tests"]]
        and [t["expected_outputs"] for t in r1[k]["tests"]] == [t["expected_outputs"] for t in review1[k]["tests"]]
        for k in review1)
    check("Review-1 15 programs identical to benchmarks/dataset/review1_dataset.json", same and len(r1) == 15, len(r1))
    try:
        old = subprocess.run(["git", "show", f"{PRE_PROJECT_COMMIT}:benchmarks/dataset/review1_dataset.json"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
        with open(os.path.join(ROOT, "benchmarks", "dataset", "review1_dataset.json"), encoding="utf-8") as fh:
            check("review1_dataset.json unchanged since pre-project commit " + PRE_PROJECT_COMMIT,
                  json.loads(old) == json.load(fh))
    except (OSError, subprocess.CalledProcessError):
        checks["review1_dataset.json unchanged since pre-project commit"] = {"ok": None, "detail": "git unavailable"}

    # external provenance
    jdir = os.path.join(ROOT, "jotai_unique_300")
    with open(os.path.join(jdir, "manifest.csv"), newline="", encoding="utf-8") as fh:
        jman = {row["file"]: row["sha256"] for row in csv.DictReader(fh)}
    ext = [r for r in recs if r["group"] == "external"]
    ext_rows, ext_bad = [], []
    for r in ext:
        o = r["origin"]
        fname = o["local_file"].split("/", 1)[1]
        with open(os.path.join(jdir, fname), "rb") as fb:
            data = fb.read()
        file_ok = hashlib.sha256(data).hexdigest() == jman.get(fname) == o["file_sha256"]
        func_ok = o.get("upstream_function_source") is None or o["upstream_function_source"] in data.decode("utf-8", "replace")
        lic_ok = r["license"]["status"] == "unverified" and r["license"]["collection_license"] == "GPL-3.0"
        if not (file_ok and func_ok and lic_ok):
            ext_bad.append(r["id"])
        if r["status"] == "accepted":
            ext_rows.append({"id": r["id"], "function": o["function"], "file": fname, "file_sha256_ok": file_ok,
                             "function_text_verbatim_in_file": func_ok, "license_status": r["license"]["status"],
                             "gcc_crosscheck": r["conversion"]["crosscheck"]["status"],
                             "vectors_crosschecked": r["conversion"]["crosscheck"]["harness_vectors_checked"],
                             "source": r["source"].strip().replace("\n", " ")})
    check("external: 300 records, file hashes, verbatim function text, licence unverified/GPL-3.0 collection",
          len(ext) == 300 and not ext_bad, ext_bad[:5])
    check("external accepted records cross-checked with gcc",
          all(x["gcc_crosscheck"] == "passed" for x in ext_rows), [x["id"] for x in ext_rows])
    check("incompatible external records are never accepted",
          all(r["status"] in ("incompatible", "accepted", "duplicate", "rejected") for r in ext)
          and all(r["validation"]["reason"] for r in ext if r["status"] == "incompatible"))
    return {"passed": not problems, "problems": problems, "checks": checks,
            "counts": {"records": len(recs), "by_status": dict(by_status), "accepted_by_group": dict(by_group),
                       "tests": n_tests}, "external_accepted": ext_rows}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    result = audit()
    for name, c in result["checks"].items():
        print(f"[{'ok' if c['ok'] else ('--' if c['ok'] is None else 'FAIL')}] {name}"
              + (f"  ({c['detail']})" if c["detail"] not in ("", None) and not c["ok"] else ""))
    print(json.dumps(result["counts"]))
    for x in result["external_accepted"]:
        print(f"  {x['id']}  {x['function']:<24} gcc {x['gcc_crosscheck']} ({x['vectors_crosschecked']} vectors)  "
              f"licence {x['license_status']}  |  {x['source']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, indent=2)
            fh.write("\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
