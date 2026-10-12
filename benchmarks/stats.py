"""Dataset statistics (JSON + CSV), computed only from manifest records."""

import csv
import json
import os
from collections import Counter, defaultdict

from optimizer.mutations import OPERATORS

STATUSES = ("accepted", "rejected", "duplicate", "incompatible")
LENGTH_BINS = ((0, 10), (11, 20), (21, 40), (41, 80), (81, 10 ** 9))


def length_bin(n):
    for lo, hi in LENGTH_BINS:
        if lo <= n <= hi:
            return f"{lo}-{hi}" if hi < 10 ** 9 else f">{lo - 1}"
    return "?"


def compute_statistics(records, dedup_report=None):
    by_status = Counter(r["status"] for r in records)
    by_group = defaultdict(Counter)
    by_cat = defaultdict(Counter)
    reasons = defaultdict(Counter)
    licenses = defaultdict(Counter)
    lengths = defaultdict(Counter)
    opp_programs = defaultdict(Counter)
    opp_sites = defaultdict(Counter)
    tests = Counter()
    for r in records:
        g = r["group"]
        by_group[g][r["status"]] += 1
        by_cat[(g, r["subgroup"], r["category"])][r["status"]] += 1
        if r["status"] != "accepted":
            reasons[g][r["validation"].get("reason") or r["status"]] += 1
        licenses[g][r["license"]["status"]] += 1
        if r["status"] != "accepted":
            continue
        lengths[g][length_bin(r["n_tac_instructions"])] += 1
        for op, n in r["opportunities"].items():
            opp_sites[g][op] += n
            opp_programs[g][op] += n > 0
        tests["accepted_programs"] += 1
        tests["tests"] += len(r["tests"])
        tests["tests_expecting_error"] += sum(t["expected_error"] is not None for t in r["tests"])
        tests["programs_with_error_tests"] += any(t["expected_error"] is not None for t in r["tests"])
        tests["int32_safe_programs"] += bool(r["int32_safe_on_tests"])
    accepted = [r for r in records if r["status"] == "accepted"]
    sizes = sorted(r["n_tac_instructions"] for r in accepted)
    return {
        "totals": {"candidates": len(records), **{s: by_status.get(s, 0) for s in STATUSES}},
        "by_group": {g: {s: c.get(s, 0) for s in STATUSES} for g, c in by_group.items()},
        "by_category": [{"group": g, "subgroup": sg, "category": cat, **{s: c.get(s, 0) for s in STATUSES}}
                        for (g, sg, cat), c in by_cat.items()],
        "review1_members_accepted": sum(1 for r in accepted if r["review1_member"]),
        "rejection_reasons": {g: dict(c) for g, c in reasons.items()},
        "license_status": {g: dict(c) for g, c in licenses.items()},
        "tac_length_bins_accepted": {g: dict(c) for g, c in lengths.items()},
        "tac_length_accepted": {"min": sizes[0], "median": sizes[len(sizes) // 2], "max": sizes[-1],
                                "mean": round(sum(sizes) / len(sizes), 2)} if sizes else {},
        "operator_opportunity_programs": {g: {op: c.get(op, 0) for op, _ in OPERATORS} for g, c in opp_programs.items()},
        "operator_opportunity_sites": {g: {op: c.get(op, 0) for op, _ in OPERATORS} for g, c in opp_sites.items()},
        "uniqueness": {
            "accepted_unique_source_hashes": len({r["source_sha256"] for r in accepted}),
            "accepted_unique_canonical_tac": len({r["canonical_tac_sha256"] for r in accepted}),
            "duplicate_records": (dedup_report or {}).get("n_duplicate_records", by_status.get("duplicate", 0)),
        },
        "tests": dict(tests),
    }


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_statistics(stats, out_dir):
    with open(os.path.join(out_dir, "statistics.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(stats, fh, indent=2)
        fh.write("\n")
    _write_csv(os.path.join(out_dir, "statistics_by_category.csv"),
               ["group", "subgroup", "category", *STATUSES],
               [[c["group"], c["subgroup"], c["category"], *(c[s] for s in STATUSES)] for c in stats["by_category"]])
    _write_csv(os.path.join(out_dir, "statistics_rejections.csv"), ["group", "reason", "count"],
               [[g, k, v] for g, c in stats["rejection_reasons"].items() for k, v in sorted(c.items())])
    _write_csv(os.path.join(out_dir, "statistics_lengths.csv"), ["group", "tac_instructions", "accepted_programs"],
               [[g, k, v] for g, c in stats["tac_length_bins_accepted"].items() for k, v in c.items()])
    _write_csv(os.path.join(out_dir, "statistics_opportunities.csv"),
               ["group", "operator", "programs_with_opportunity", "total_sites"],
               [[g, op, n, stats["operator_opportunity_sites"][g][op]]
                for g, c in stats["operator_opportunity_programs"].items() for op, n in c.items()])
    _write_csv(os.path.join(out_dir, "statistics_licenses.csv"), ["group", "license_status", "count"],
               [[g, k, v] for g, c in stats["license_status"].items() for k, v in c.items()])
