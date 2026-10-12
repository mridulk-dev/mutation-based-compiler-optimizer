"""Dashboard API: serves real artifacts, runs real compiles, rejects bad input."""

import json
import threading
import unittest
import urllib.error
import urllib.request

from dashboard import server


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = server.make_server("127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=60) as r:
            return r.status, r.read()

    def post(self, body):
        req = urllib.request.Request(self.base + "/api/compile", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_static_and_health(self):
        self.assertEqual(self.get("/")[0], 200)
        self.assertIn(b"app.js", self.get("/")[1])
        self.assertEqual(json.loads(self.get("/api/health")[1])["ok"], True)

    def test_dataset_api_matches_manifest(self):
        recs = json.loads(self.get("/api/dataset")[1])["records"]
        self.assertEqual(len(recs), len(server.manifest()))
        status, body = self.get("/api/dataset/P01")
        self.assertEqual(json.loads(body)["id"], "P01")

    def test_compile_real_optimizer(self):
        code, r = self.post({"source": "input a, b; x = a * 2 + 0; y = (a + b) * (a + b); print x + y;",
                             "inputs": "3 4\n-1 5", "strategies": ["greedy", "fixed"]})
        self.assertEqual(code, 200)
        self.assertTrue(r["ok"])
        self.assertLess(r["results"]["greedy"]["final_cost"], 1.0)
        self.assertTrue(all(c["match"] for c in r["results"]["greedy"]["checks"]))
        self.assertEqual(sum(c["user"] for c in r["results"]["greedy"]["checks"]), 2)

    def test_compile_reports_errors(self):
        code, r = self.post({"source": "input a; print b;"})
        self.assertEqual((code, r["ok"], r["stage"]), (200, False, "parse"))
        code, r = self.post({"source": "input a; print a;", "inputs": "1 2"})
        self.assertEqual((code, r["ok"]), (400, False))
        code, r = self.post({"source": "x" * 10000})
        self.assertEqual(code, 400)
        code, r = self.post({"source": "input a; print a;", "strategies": ["rm -rf"]})
        self.assertEqual(code, 400)

    def test_unsafe_operator_rejections_visible(self):
        code, r = self.post({"source": "input a, b; x = a - b; y = x * 3; print y; print x;", "unsafe": True,
                             "strategies": ["greedy"]})
        self.assertGreater(r["results"]["greedy"]["candidates_rejected"], 0)
        self.assertTrue(all(c["match"] for c in r["results"]["greedy"]["checks"]))

    def test_no_path_traversal(self):
        for path in ("/files/dataset/../../README.md", "/files/dataset/..%2F..%2FREADME.md", "/../README.md",
                     "/files/secret/x", "/files/results/../../main.py"):
            with self.assertRaises(urllib.error.HTTPError, msg=path) as cm:
                self.get(path)
            self.assertEqual(cm.exception.code, 404)
        self.assertEqual(self.get("/files/dataset/statistics.json")[0], 200)


if __name__ == "__main__":
    unittest.main()
