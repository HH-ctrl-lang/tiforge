import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from harness.core.agentarts import build_agent_report, request_proposal
from harness.core.evidence import EvidenceStore, SCHEMA, export_run


class AgentArtsTests(unittest.TestCase):
    def test_model_agent_reads_local_key_and_prefers_environment(self):
        with TemporaryDirectory() as directory:
            settings = Path(directory) / "maas.local.json"
            settings.write_text('{"api_key": " localhost-test-key "}', encoding="utf-8-sig")
            with patch("harness.core.model_agent.LOCAL_SETTINGS", settings), patch.dict(
                "os.environ", {"TIFORGE_MAAS_API_KEY": ""}
            ), patch("harness.core.model_agent.getpass", side_effect=AssertionError("unexpected prompt")):
                from harness.core.model_agent import read_api_key

                self.assertEqual(read_api_key(), "localhost-test-key")
                with patch.dict("os.environ", {"TIFORGE_MAAS_API_KEY": "override-test-key"}):
                    self.assertEqual(read_api_key(), "override-test-key")

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        self.manifest = {"run_id": "tf-test", "case_id": "industrial-cost", "workflow_version": "v08"}
        self.scope = {key: self.manifest[key] for key in ("case_id", "workflow_version", "run_id")}
        node = {
            "run_id": "tf-test", "node_id": "requirements", "node_name": "研究需求解析",
            "outputs": {
                "prompt": "private prompt", "systemFields": {"authorization": "private key"},
                "userFields": {"literature_query": "", "unapproved": "private value"},
            },
        }
        (directory / "nodes.jsonl").write_text(json.dumps(node), encoding="utf-8")
        self.run = export_run(directory, self.manifest)
        snapshot = directory / "snapshot.json"
        snapshot.write_text(json.dumps({"schema": SCHEMA, "runs": [self.run]}), encoding="utf-8")
        self.store = EvidenceStore(snapshot)
        self.evidence = self.store.read_run_evidence("tf-test", "requirements")["evidence"]
        self.target = next(item for item in self.evidence if item["field"] == "outputs.literature_query")
        self.report = {
            "case_id": "industrial-cost", "workflow_version": "v08", "run_ids": ["tf-test"],
            "status": "proposed", "missing_evidence": [],
            "proposals": [{
                "target_node": "requirements", "target_field": "outputs.literature_query",
                "evidence_ids": [self.target["evidence_id"]], "draft_change": "生成有效查询",
                "acceptance": ["查询非空"],
            }],
        }

    def test_export_masks_payload_and_distinguishes_missing_from_empty(self):
        self.assertNotIn("private", json.dumps(self.run))
        fields = {item["field"]: item["value"] for item in self.evidence}
        self.assertEqual(fields["inputs.literature_query"], "missing")
        self.assertEqual(fields["outputs.literature_query"], "empty")
        self.assertEqual(self.store.read_run_evidence("../private"), {"error": "run_not_found"})
        self.assertEqual(self.store.list_runs("industrial-cost", "v09"), {"runs": []})

    def test_sse_response_and_tool_results_are_used_to_validate_report(self):
        wrapped = {"content": [{"type": "text", "text": json.dumps({"evidence": self.evidence})}]}
        events = [
            {"event": "plugin_end", "content": {"result": wrapped}},
            {"event": "summary_response", "content": json.dumps(self.report), "executionId": "exec-test"},
            {"event": "end"},
        ]
        lines = [line for event in events for line in (f"data: {json.dumps(event)}", "")]
        with patch.dict("os.environ", {
            "TIFORGE_PROPOSER_URL": "https://agent.example/runtimes/test/invocations",
            "TIFORGE_PROPOSER_API_KEY": "example",
        }), patch("harness.core.agentarts.requests.post") as post:
            response = post.return_value.__enter__.return_value
            response.status_code = 200
            response.iter_lines.return_value = lines
            result = build_agent_report(self.store, **self.scope)
            response.iter_lines.return_value = lines[:-2]
            with self.assertRaisesRegex(ValueError, "未返回完整回答"):
                request_proposal(self.scope)
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["agentarts"]["execution_id"], "exec-test")
        self.assertEqual(set(post.call_args.kwargs["json"]), {"query"})

    def test_invented_modified_or_unobserved_evidence_is_rejected(self):
        key = self.target["evidence_id"]
        for records in ({}, {key: {**self.target, "value": "nonempty"}}):
            with self.subTest(records=records), patch(
                "harness.core.agentarts.request_proposal", return_value=(self.report, records, {})
            ):
                result = build_agent_report(self.store, **self.scope)
                self.assertEqual(result["status"], "needs_review")
                self.assertEqual(result["proposals"], [])

    def test_interrupted_archive_does_not_support_a_proposal(self):
        self.store.runs["tf-test"]["capture_incomplete"] = True
        records = {self.target["evidence_id"]: self.target}
        with patch("harness.core.agentarts.request_proposal", return_value=(self.report, records, {})):
            result = build_agent_report(self.store, **self.scope)
        self.assertEqual(result["status"], "needs_review")

    def test_model_agent_executes_only_scoped_tools_and_validates_their_results(self):
        calls = [
            {"id": "outside", "type": "function", "function": {
                "name": "read_run_evidence", "arguments": '{"run_id":"another-run"}',
            }},
            {"id": "evidence", "type": "function", "function": {
                "name": "read_run_evidence", "arguments": '{"run_id":"tf-test","node":"requirements"}',
            }},
        ]
        with patch.dict("os.environ", {"TIFORGE_MAAS_API_KEY": "test-key"}), patch(
            "harness.core.model_agent.requests.Session.post"
        ) as post:
            def respond(_url, **kwargs):
                messages = list(kwargs["json"]["messages"])
                response = MagicMock()
                response.__enter__.return_value = response
                response.status_code = 200
                if len(messages) == 2:
                    message = {"role": "assistant", "tool_calls": calls}
                elif len(messages) == 5:
                    self.assertIn("not_allowed_in_this_investigation", messages[-2]["content"])
                    self.assertIn(self.target["evidence_id"], messages[-1]["content"])
                    message = {"role": "assistant", "content": "以下是修改建议。"}
                else:
                    self.assertEqual(len(messages), 6)
                    self.assertIn("合法 JSON 对象", messages[-1]["content"])
                    message = {"role": "assistant", "content": "```json\n" + json.dumps(self.report) + "\n```"}
                response.json.return_value = {"choices": [{"message": message}]}
                return response

            post.side_effect = respond
            result = build_agent_report(self.store, **self.scope, source="model_agent")
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(result["model_agent"]["tool_calls"], 2)
        self.assertEqual(post.call_count, 3)
