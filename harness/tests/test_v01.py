import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness.engine.v01 import DESIGN, FORMAT_REPLY, GATE, LITERATURE, REVIEW, compare, evaluate


class DevelopmentHarnessTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.run = Path(directory.name) / "tf-fixture"
        self.run.mkdir()
        self.manifest = {"workflow_status": "succeeded"}
        self.expected = {"route": "design", "candidate_count": 1, "allowed_elements": ["Ti"]}
        decisions = [{"candidate_id": "C01", "status": "needs_review"}]
        literature = {"status": "no_evidence", "records": [], "missing_evidence": ["未找到 Ti999"]}
        handoff = {f"literature_{key}": value for key, value in literature.items()}
        self.nodes = {
            "研究需求解析": {"outputs": {"clarification_needed": False, "literature_query": "查询 Ti999"}},
            LITERATURE: {"outputs": literature},
            DESIGN: {
                "inputs": {"userFields": dict(handoff)},
                "outputs": {"candidate_list": [{"candidate_id": "C01", "composition_wt_pct": "Ti:100"}]},
            },
            GATE: {"outputs": {"userFields": {
                "candidate_decisions": decisions, "all_passed": False,
                "physical_checks": [{"candidate_id": "C01", "rule_id": "COMPOSITION", "status": "pass"}],
            }}},
            REVIEW: {
                "inputs": {"userFields": {**handoff, "candidate_decisions": decisions}},
                "outputs": {"candidate_reviews": [{"candidate_id": "C01"}]},
            },
            FORMAT_REPLY: {"inputs": {"candidate_decisions": decisions}},
            "结束": {"outputs": {"responseContent": "C01｜物理守门：待复核"}},
        }
        self.write_outcome("gate_pending")

    def write_outcome(self, outcome):
        (self.run / "scientific_outcomes.jsonl").write_text(
            json.dumps({"candidate_id": "C01", "outcome_type": outcome}), encoding="utf-8"
        )

    def evaluate_run(self):
        (self.run / "nodes.jsonl").write_text("\n".join(
            json.dumps({"node_name": name, **node}) for name, node in self.nodes.items()
        ), encoding="utf-8")
        return evaluate(self.run, self.manifest, self.expected)

    def test_valid_pending_candidate_passes_structural_checks(self):
        result = self.evaluate_run()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["evidence"]["gate_states"], {"C01": "needs_review"})

    def test_reference_check_is_kept_as_evidence_without_changing_the_result(self):
        fields = self.nodes[GATE]["outputs"]["userFields"]
        fields.update(version="0.4.5", profile_sha256="test-hash", profile_purpose="provisional")
        reference = {
            "candidate_id": "C01", "rule_id": "MO_EQ_REFERENCE", "status": "needs_review",
            "metrics_json": json.dumps({"unsupported_elements": ["O"]}),
            "source": "reference source", "version": "F02", "reason": "缺少 O 的系数",
        }
        fields["physical_checks"].append(reference)
        result = self.evaluate_run()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["evidence"]["physical_checks"][-1], reference)
        self.assertEqual(result["evidence"]["gate_profile_sha256"], "test-hash")

    def test_independent_evaluator_uses_v01_for_pending_candidate(self):
        from harness.core.evaluate import evaluate_run

        self.evaluate_run()
        self.assertEqual(evaluate_run(self.run, self.manifest, self.expected), ("pass", []))
        self.write_outcome("gate_rejected")
        status, issues = evaluate_run(self.run, self.manifest, self.expected)
        self.assertEqual(status, "fail")
        self.assertIn("archive_gate_mapping", [issue["code"] for issue in issues])

    def test_pending_archived_as_rejected_fails(self):
        self.write_outcome("gate_rejected")
        result = self.evaluate_run()
        self.assertEqual(result["status"], "fail")
        self.assertIn("archive_gate_mapping", [item["code"] for item in result["findings"]])

    def test_lost_no_evidence_status_fails(self):
        self.nodes[REVIEW]["inputs"]["userFields"]["literature_status"] = "success"
        result = self.evaluate_run()
        self.assertEqual(result["status"], "fail")
        self.assertIn("literature_handoff", [item["code"] for item in result["findings"]])

    def test_capture_error_requires_review(self):
        self.manifest["capture_error"] = {"kind": "incomplete_stream"}
        self.assertEqual(self.evaluate_run()["status"], "needs_review")

    def test_comparison_requires_full_suite_after_development_screen(self):
        good = self.evaluate_run()
        self.write_outcome("gate_rejected")
        bad = self.evaluate_run()
        baseline = {"suite_sha256": "same", "workflow_version": "v08", "cases": {"design": bad}}
        good["evidence"]["run_id"] = "tf-different"
        candidate = {"suite_sha256": "same", "workflow_version": "v09", "cases": {"design": good}}
        result = compare(baseline, candidate, "design", "archive_gate_mapping")
        self.assertEqual(result["decision"], "ready_for_full_regression")

    def test_comparison_surfaces_known_guard_profile_change(self):
        good = self.evaluate_run()
        self.write_outcome("gate_rejected")
        bad = self.evaluate_run()
        good["evidence"].update(run_id="tf-different", gate_version="0.4.5", gate_profile_sha256="new")
        bad["evidence"].update(gate_version="0.4.4", gate_profile_sha256="old")
        baseline = {"suite_sha256": "same", "workflow_version": "v08", "cases": {"design": bad}}
        candidate = {"suite_sha256": "same", "workflow_version": "v09", "cases": {"design": good}}
        result = compare(baseline, candidate, "design", "archive_gate_mapping")
        self.assertEqual(result["decision"], "needs_review")
        self.assertEqual([row["field"] for row in result["gate_environment_differences"]],
                         ["gate_version", "gate_profile_sha256"])
