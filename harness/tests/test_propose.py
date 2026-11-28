import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness.core.propose import LITERATURE, REQUIREMENTS, build_proposals


class ProposalTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.cases = {"cost": {
            "inputs": {"query": "为工业结构件设计钛合金。"},
            "expected": {"route": "design", "candidate_count": 1, "allowed_elements": ["Ti"]},
        }}

    def write_run(self, run_id, *, parsed="", sent="", capture_error=None):
        run = self.root / "batch" / run_id
        run.mkdir(parents=True)
        manifest = {
            "run_id": run_id, "case_id": "cost", "workflow_version": "v08",
            "workflow_status": "failed" if capture_error else "succeeded",
            "capture_error": capture_error,
        }
        (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (run / "request.json").write_text(json.dumps(
            {**manifest, "inputs": self.cases["cost"]["inputs"]}
        ), encoding="utf-8")
        nodes = [
            {"node_name": REQUIREMENTS, "outputs": {"clarification_needed": False, "literature_query": parsed}},
            {"node_name": LITERATURE, "inputs": {"userFields": {"literature_query": sent}},
             "outputs": {"status": "error", "error_code": "EMPTY_QUERY", "records": []}},
        ]
        (run / "nodes.jsonl").write_text("\n".join(map(json.dumps, nodes)), encoding="utf-8")

    def test_groups_repeated_earliest_deviation_and_excludes_capture_errors(self):
        self.write_run("tf-1")
        self.write_run("tf-2")
        self.write_run("tf-3", capture_error={"kind": "request_error"})
        report = build_proposals(self.cases, [self.root, self.root / "batch"])
        proposal, = report["proposals"]
        self.assertEqual(report["counts"], {"fail": 2, "needs_review": 1})
        self.assertEqual(proposal["first_observed_deviation"]["node"], REQUIREMENTS)
        self.assertEqual([item["run_id"] for item in proposal["evidence"]], ["tf-1", "tf-2"])
        self.assertEqual(proposal["predicted_fixes"], ["cost"])
        self.assertEqual(report["unresolved"][0]["run_id"], "tf-3")
        self.assertEqual(build_proposals(self.cases, [self.root])["proposals"], [proposal])

    def test_locates_binding_when_parser_produced_a_query(self):
        self.write_run("tf-1", parsed="钛合金与成本约束")
        result = build_proposals(self.cases, [self.root])
        proposal, = result["proposals"]
        self.assertEqual(proposal["first_observed_deviation"]["node"], LITERATURE)
        self.assertEqual(proposal["first_observed_deviation"]["field"], "inputs.literature_query")
        self.assertIn("绑定", proposal["draft_change"])

    def test_does_not_guess_if_the_recorded_tool_input_was_nonempty(self):
        self.write_run("tf-1", parsed="钛合金", sent="钛合金")
        report = build_proposals(self.cases, [self.root])
        self.assertEqual(report["proposals"], [])
        self.assertEqual(report["unresolved"][0]["status"], "fail")
