import json
import unittest
from collections import Counter

from benchmarks.generate_dataset import CATEGORY_PLAN, DATASET_PATH, generate
from execution.interpreter import run
from frontend.tac import compile_source


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = generate(2026)

    def test_generation_is_deterministic(self):
        self.assertEqual(generate(2026), self.dataset)
        self.assertNotEqual(generate(7), self.dataset)

    def test_committed_file_matches_generator(self):
        with open(DATASET_PATH, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), self.dataset)

    def test_shape(self):
        progs = self.dataset["programs"]
        self.assertEqual(len(progs), 15)
        self.assertEqual(Counter(p["category"] for p in progs), Counter(dict(CATEGORY_PLAN)))
        self.assertEqual(len({p["id"] for p in progs}), 15)
        for p in progs:
            self.assertTrue(3 <= len(p["tests"]) <= 5, p["id"])

    def test_expected_outputs_match_compiled_tac(self):
        for p in self.dataset["programs"]:
            program = compile_source(p["source"])
            for t in p["tests"]:
                r = run(program, t["inputs"])
                self.assertEqual(list(r.outputs), t["expected_outputs"], p["id"])
                self.assertEqual(r.error is not None, t["expected_error"], p["id"])


if __name__ == "__main__":
    unittest.main()
