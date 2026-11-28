import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness.engine.v01 import audit
from harness.core.evaluate import evaluate_run, load_cases, load_run


class IndependentEvaluationTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.run_dir = Path(directory.name) / "tf-check"
        self.run_dir.mkdir()
        self.manifest = {
            "case_id": "composition-total",
            "run_id": self.run_dir.name,
            "workflow_version": "v0.4.4",
            "workflow_status": "succeeded",
        }
        self.expected = {
            "route": "failure_handling",
            "checks": [{
                "type": "hard_constraint_violation",
                "node": "材料成分约束检查",
                "field": "candidate_decisions",
                "where": {"candidate_id": "C01"},
                "equals": {"status": "reject", "rule_id": "COMPOSITION"},
                "required_text": ["reason"],
            }],
        }

    def check(self, *nodes):
        (self.run_dir / "nodes.jsonl").write_text("\n".join(map(json.dumps, nodes)), encoding="utf-8")
        return evaluate_run(self.run_dir, self.manifest, self.expected)

    def gate_output(self, *decisions):
        return {
            "node_name": "材料成分约束检查",
            "outputs": {"userFields": {"candidate_decisions": list(decisions)}},
        }

    def test_correctly_rejecting_an_invalid_candidate_passes_evaluation(self):
        gate = self.gate_output(
            {"candidate_id": "C02", "status": "pass"},
            {
                "candidate_id": "C01", "status": "reject",
                "rule_id": "COMPOSITION", "reason": "成分总和不等于100%",
            },
        )
        self.assertEqual(self.check(gate), ("pass", []))

    def test_wrong_disposition_fails_even_with_incomplete_details(self):
        status, issues = self.check(self.gate_output({"candidate_id": "C01", "status": "pass"}))
        self.assertEqual(status, "fail")
        self.assertTrue(any("status 与固定预期不符" in issue for issue in issues))

    def test_missing_or_ambiguous_evidence_requires_review(self):
        decision = {"candidate_id": "C01", "status": "reject", "rule_id": "COMPOSITION"}
        for rows in ((decision,), (decision, decision), ()):
            with self.subTest(rows=rows):
                self.assertEqual(self.check(self.gate_output(*rows))[0], "needs_review")

    def test_expected_tool_stop_still_requires_matching_error(self):
        self.manifest["workflow_status"] = "failed"
        self.expected = {
            "route": "failure_handling",
            "workflow_status": "failed",
            "checks": [{
                "type": "tool_error",
                "node": "材料成分约束检查",
                "source": "error",
                "equals": {"code": "INVALID_ARGUMENT"},
            }],
        }
        self.assertEqual(self.check({"node_name": "材料成分约束检查", "error": {"code": "INVALID_ARGUMENT"}}),
                         ("pass", []))
        self.assertEqual(self.check({"node_name": "材料成分约束检查", "error": {"code": "TIMEOUT"}})[0], "fail")

    def test_loads_failure_cases_and_checks_actual_inputs(self):
        inputs = {"query": "检查指定的候选。"}
        case = {"case_id": self.manifest["case_id"], "inputs": inputs, "expected": self.expected}
        suite = self.run_dir.parent / "cases.jsonl"
        suite.write_text(json.dumps(case), encoding="utf-8")
        cases = load_cases(suite)
        (self.run_dir / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        request = {**self.manifest, "inputs": {"query": "另一条输入"}}
        (self.run_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "实际输入"):
            load_run(self.run_dir, cases)

    def test_batch_audit_uses_the_evaluator_for_failure_handling_cases(self):
        inputs = {"query": "检查指定的候选。"}
        case_id = self.manifest["case_id"]
        suite = self.run_dir.parent / "cases.jsonl"
        suite.write_text(json.dumps(
            {"case_id": case_id, "inputs": inputs, "expected": self.expected}
        ), encoding="utf-8")
        (self.run_dir / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.run_dir / "request.json").write_text(json.dumps(
            {**self.manifest, "inputs": inputs}
        ), encoding="utf-8")
        single_status, single_issues = self.check(self.gate_output(
            {"candidate_id": "C01", "status": "pass", "rule_id": "COMPOSITION", "reason": "检查完成"}
        ))

        result = audit(load_cases(suite), self.run_dir.parent)["cases"][case_id]
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["status"], single_status)
        self.assertEqual([item["detail"] for item in result["findings"]], single_issues)


if __name__ == "__main__":
    unittest.main()
