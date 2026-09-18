import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import jev
from scenarios import cases


class EvaluationTests(unittest.TestCase):
    def test_labels_do_not_leak_into_request(self):
        body = jev.payload(cases()[0], "test-model")
        self.assertEqual(set(body), {"model", "state", "questions"})

    def test_brier_and_threshold(self):
        case = {"questions": {"x": {"type": "noul"}}, "expected": {"x": False}}
        result = jev.evaluate(case, {"answers": {"x": {"noul": 0.2}}})[0]
        self.assertTrue(result["correct"])
        self.assertAlmostEqual(result["brier"], 0.04)

    def test_invalid_probabilities_rejected(self):
        for value in [-1, 1.1, float("nan"), True, "0.8"]:
            with self.assertRaises(ValueError):
                jev.probability(value)

    def test_missing_answer_is_error(self):
        with self.assertRaises(ValueError):
            jev.evaluate(cases()[0], {"answers": {}})

    def test_report_escapes_untrusted_content(self):
        case = cases()[0]
        case["state"] = "<script>alert(1)</script>"
        row = dict(case=case, response=jev.demo_response(case), checks=[], elapsed_s=0)
        with tempfile.TemporaryDirectory() as temp:
            jev.write_report([row], Path(temp), "test", True)
            report = (Path(temp) / "report.html").read_text()
            self.assertNotIn("<script>", report)
            self.assertIn("&lt;script&gt;", report)
            self.assertTrue(json.loads((Path(temp) / "results.json").read_text())["synthetic"])

    def test_request_uses_decisions_endpoint(self):
        import io
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"answers": {}}')) as mock:
            jev.request({"state": "test"}, "fake-key", 5)
        req = mock.call_args.args[0]
        self.assertEqual(req.full_url, jev.ENDPOINT)
        self.assertEqual(req.get_method(), "POST")


if __name__ == "__main__":
    unittest.main()
