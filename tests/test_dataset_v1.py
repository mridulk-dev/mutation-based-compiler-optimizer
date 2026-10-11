"""Checks on the committed dataset release (dataset/miniarith-v1)."""

import hashlib
import json
import os
import unittest
from collections import Counter

from benchmarks.build import normalize_source
from execution.reference import run_source_detailed
from frontend.tac import canonical_tac, compile_source, format_tac

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS = os.path.join(ROOT, "dataset", "miniarith-v1")
REQUIRED = ("id", "status", "group", "subgroup", "category", "review1_member", "origin", "attribution", "license",
            "validation")
ACCEPTED_REQUIRED = ("source", "source_sha256", "normalized_source_sha256", "tac", "tac_sha256",
                     "canonical_tac_sha256", "tests", "n_inputs", "opportunities", "int32_safe_on_tests")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DatasetV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(DS, "manifest.jsonl"), encoding="utf-8") as fh:
            cls.records = [json.loads(line) for line in fh]
        with open(os.path.join(DS, "statistics.json"), encoding="utf-8") as fh:
            cls.stats = json.load(fh)
        cls.accepted = [r for r in cls.records if r["status"] == "accepted"]

    def test_fields_and_unique_ids(self):
        self.assertEqual(len({r["id"] for r in self.records}), len(self.records))
        for r in self.records:
            for k in REQUIRED:
                self.assertIn(k, r, r["id"])
        for r in self.accepted:
            for k in ACCEPTED_REQUIRED:
                self.assertIn(k, r, r["id"])

    def test_statistics_agree_with_manifest(self):
        counts = Counter(r["status"] for r in self.records)
        for status in ("accepted", "rejected", "duplicate", "incompatible"):
            self.assertEqual(self.stats["totals"][status], counts.get(status, 0))
        self.assertEqual(self.stats["totals"]["candidates"], len(self.records))
        self.assertGreaterEqual(len(self.accepted), 300)

    def test_review1_programs_unchanged(self):
        with open(os.path.join(ROOT, "benchmarks", "dataset", "review1_dataset.json"), encoding="utf-8") as fh:
            review1 = {p["id"]: p for p in json.load(fh)["programs"]}
        mine = {r["id"]: r for r in self.records if r["review1_member"]}
        self.assertEqual(set(mine), set(review1))
        for pid, p in review1.items():
            self.assertEqual(mine[pid]["source"], p["source"])
            self.assertEqual(mine[pid]["status"], "accepted")
            self.assertEqual([t["inputs"] for t in mine[pid]["tests"]], [t["inputs"] for t in p["tests"]])
            self.assertEqual([t["expected_outputs"] for t in mine[pid]["tests"]],
                             [t["expected_outputs"] for t in p["tests"]])

    def test_hashes_and_tac(self):
        for r in self.accepted:
            program = compile_source(r["source"])
            self.assertEqual(r["source_sha256"], sha(r["source"]))
            self.assertEqual(r["normalized_source_sha256"], sha(normalize_source(r["source"])))
            self.assertEqual(r["tac"], format_tac(program, numbered=False))
            self.assertEqual(r["tac_sha256"], sha(r["tac"]))
            self.assertEqual(r["canonical_tac_sha256"], sha(format_tac(canonical_tac(program), numbered=False)))

    def test_expected_outputs_come_from_reference(self):
        for r in self.accepted:
            for t in r["tests"]:
                ref = run_source_detailed(r["source"], t["inputs"])
                self.assertEqual(list(ref.outputs), t["expected_outputs"], r["id"])
                self.assertEqual(ref.error, t["expected_error"], r["id"])

    def test_no_duplicates_among_accepted(self):
        for key in ("normalized_source_sha256", "canonical_tac_sha256"):
            values = [r[key] for r in self.accepted]
            self.assertEqual(len(values), len(set(values)), key)
        ids = {r["id"] for r in self.records}
        for r in self.records:
            if r["status"] == "duplicate":
                self.assertIn(r["duplicate_of"], ids)

    def test_external_provenance_and_license(self):
        ext = [r for r in self.records if r["group"] == "external"]
        self.assertEqual(len(ext), 300)
        for r in ext:
            self.assertTrue(r["origin"]["local_file"].startswith("jotai_unique_300/"))
            self.assertTrue(r["origin"]["file_hash_verified"])
            self.assertEqual(r["license"]["status"], "unverified")
            if r["status"] == "incompatible":
                self.assertTrue(r["validation"]["reason"])
            if r["status"] == "accepted":
                self.assertIn("conversion", r)
                self.assertTrue(r["int32_safe_on_tests"])

    def test_generated_records_reproducible(self):
        from benchmarks import randomgen, templates
        for r in self.accepted:
            o = r["origin"]
            if o.get("generator") == "benchmarks/templates.py":
                seed, pid = o["seed"].split(":", 1)
                src = templates.generate_program(int(seed), pid, r["category"], o["config"]["size"],
                                                 o["config"]["redundancy"])
                self.assertEqual(src, r["source"])
            elif o.get("generator") == "benchmarks/randomgen.py":
                seed, pid = o["seed"].split(":", 1)
                self.assertEqual(randomgen.generate_program(int(seed), pid), r["source"])


if __name__ == "__main__":
    unittest.main()
