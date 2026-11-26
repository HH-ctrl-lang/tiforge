import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.archive_runtime_run import archive_run
from scripts.diagnose_runs import diagnose_archive, load_jsonl


class V08ArchiveTest(unittest.TestCase):
    def archive(self, status, **tool_output):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        run = Path(temporary.name) / "tf-v08-test"
        run.mkdir()
        self.assertFalse((run / "manifest.json").exists())
        lifecycle = {
            "node_id": "literature",
            "node_name": "文献知识提取",
            "node_type": "jiuwen.mcp",
            "status": {"desc": "succeeded"},
            "inputs": {"literature_query": "查询 Ti999"},
            "outputs": {"status": status, "call_id": "pspp-test", **tool_output},
        }
        events = [
            {"event": "workflow_started", "data": {"start_time": 1}},
            {"event": "node_finished", "data": lifecycle},
            {"event": "workflow_node_message", "data": {"componentId": "literature", "outputs": {}}},
            {"event": "workflow_finished", "data": {"status": {"desc": "succeeded"}, "end_time": 2}},
        ]
        (run / "raw_events.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
        manifest = archive_run(run, {"workflow_version": "v08"})
        return run, manifest

    def test_mcp_business_error_marks_successful_workflow_for_review(self):
        run, manifest = self.archive(
            "error", error_code="EMPTY_QUERY", error_message="literature_query 不能为空", isSuccess=True
        )
        self.assertEqual(manifest["workflow_status"], "succeeded")
        self.assertEqual(manifest["diagnostic_status"], "needs_review")
        self.assertEqual(manifest["failure_count"], 1)
        failure, = load_jsonl(run / "failure_records.jsonl")
        self.assertEqual(failure["failure_type"], "tool_reported_error")
        self.assertEqual(failure["tool_status"], "error")
        self.assertEqual(failure["error_code"], "EMPTY_QUERY")
        self.assertEqual(failure["tool_call_id"], "pspp-test")
        self.assertEqual(Path(failure["evidence_ref"]), run / "nodes.jsonl")
        self.assertEqual(load_jsonl(run / "scientific_outcomes.jsonl"), [])

        node, = load_jsonl(run / "nodes.jsonl")
        self.assertTrue(node.pop("tool_result")["transport_succeeded"])
        (run / "nodes.jsonl").write_text(json.dumps(node), encoding="utf-8")
        diagnostic, failures = diagnose_archive(run)
        self.assertEqual(diagnostic["tool_error_count"], 1)
        self.assertEqual(failures[0]["error_code"], "EMPTY_QUERY")

    def test_no_evidence_is_a_scientific_outcome(self):
        run, manifest = self.archive(
            "no_evidence", records=[], missing_evidence=["未找到 Ti999"], isSuccess=True
        )
        self.assertEqual(manifest["diagnostic_status"], "normal")
        self.assertEqual(manifest["failure_count"], 0)
        outcome, = load_jsonl(run / "scientific_outcomes.jsonl")
        self.assertEqual(outcome["outcome_type"], "literature_no_evidence")
        self.assertEqual(outcome["record_ids"], [])
        self.assertEqual(outcome["missing_evidence"], ["未找到 Ti999"])
