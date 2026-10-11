"""Deduplication of dataset records.

Two programs are duplicates when they have
  * the same normalised source (comments/whitespace removed), or
  * the same canonical TAC (alpha-renamed: they differ only in identifier names).

Canonical representative = the first record in build order, which is
group priority (controlled -> randomized -> external), Review-1 members first,
then by id. So a Review-1 program is never marked as a duplicate of anything.

Nothing is deleted: duplicate records stay in the manifest with
status "duplicate", `duplicate_of` and the matching key, and every duplicate
group is listed in dedup_report.json.

    python -m benchmarks.dedup dataset/miniarith-v1/manifest.jsonl   # re-run on a manifest, print report
"""

import json
import sys


def deduplicate(records):
    """Mutates accepted records in place (status -> duplicate). Returns a report."""
    seen = {}  # (kind, hash) -> canonical id
    groups = {}
    for rec in records:
        if rec.get("status") != "accepted":
            continue
        keys = [("normalized-source", rec["normalized_source_sha256"]),
                ("canonical-tac", rec["canonical_tac_sha256"])]
        match = next(((kind, seen[(kind, h)]) for kind, h in keys if (kind, h) in seen), None)
        if match is None:
            for k in keys:
                seen[k] = rec["id"]
            continue
        kind, canonical = match
        rec["status"] = "duplicate"
        rec["duplicate_of"] = canonical
        rec["validation"]["status"] = "duplicate"
        rec["validation"]["reason"] = f"duplicate-{kind}"
        rec["validation"]["message"] = f"same {kind.replace('-', ' ')} as {canonical}"
        groups.setdefault(canonical, []).append({"id": rec["id"], "match": kind, "group": rec["group"]})
    return {
        "rule": "first record in build order (controlled > randomized > external; Review-1 first; then id) "
                "is the canonical representative; later records with the same normalised source or the same "
                "canonical (alpha-renamed) TAC are marked duplicate and kept in the manifest",
        "duplicate_groups": [{"canonical": c, "duplicates": d} for c, d in groups.items()],
        "n_duplicate_records": sum(len(d) for d in groups.values()),
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    path = argv[0] if argv else "dataset/miniarith-v1/manifest.jsonl"
    with open(path, encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh]
    for r in records:  # re-evaluate from scratch on the stored hashes
        if r["status"] == "duplicate":
            r["status"] = "accepted"
    report = deduplicate(records)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
