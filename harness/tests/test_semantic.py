from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness import semantic
from harness.core.evaluate import evaluate_archive, validate_expected


class SemanticReviewTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run = Path(tmp.name)
        self.expected = {"route": "design", "candidate_count": 1, "allowed_elements": ["Ti"],
                         "semantic_review": True}
        self.nodes = [{"node_name": "候选材料设计", "outputs": {"candidate_list": [{
            "candidate_id": "C01", "composition_wt_pct": "Ti:100",
            "design_rationale": "T1 报告试样 A 的密度为 4.5。"}]}},
            {"node_name": "文献知识提取", "outputs": {"status": "success", "records": [
                {"record_id": "T1", "observation": "试样 A 的实测密度为 4.5。"}]}}]
        self.packet = semantic.packet_for(self.run, self.nodes, self.expected)
        self.review = {"packet_sha256": self.packet["packet_sha256"], "reviewer": "fixture",
                       "judgments": [{"candidate_id": "C01", "criterion": key, "status": "pass",
                                      "reason": "该文献报告相同试样的实测值。",
                                      "claim_field": "design_rationale", "claim_quote": "T1 报告试样 A 的密度为 4.5。",
                                      "source": "literature/0/observation", "evidence_quote": "试样 A 的实测密度为 4.5。"}
                                     for key in semantic.RUBRIC]}

    def statuses(self):
        return [r["status"] for r in semantic.score(self.packet, self.review)["judgments"]]

    def test_two_grounded_judgments(self):
        self.assertEqual(self.statuses(), ["pass", "pass"])

    def test_fabricated_quote_or_identifier_only(self):
        self.review["judgments"][0]["evidence_quote"] = "未收录的句子"
        self.review["judgments"][1].update(source="literature/0/record_id", evidence_quote="T1")
        self.assertEqual(self.statuses(), ["needs_review", "needs_review"])

    def test_other_record_or_missing_dimension(self):
        nodes = deepcopy(self.nodes)
        nodes[1]["outputs"]["records"][0]["record_id"] = "T2"
        self.packet = semantic.packet_for(self.run, nodes, self.expected)
        self.review["packet_sha256"] = self.packet["packet_sha256"]
        self.review["judgments"].pop(0)
        self.assertEqual(self.statuses(), ["needs_review", "needs_review"])

    def test_similar_identifier_is_not_a_citation(self):
        candidate = self.nodes[0]["outputs"]["candidate_list"][0]
        candidate["design_rationale"] = candidate["design_rationale"].replace("T1", "T10")
        self.packet = semantic.packet_for(self.run, self.nodes, self.expected)
        self.review["packet_sha256"] = self.packet["packet_sha256"]
        for judgment in self.review["judgments"]:
            judgment["claim_quote"] = candidate["design_rationale"]
        self.assertEqual(self.statuses(), ["pass", "needs_review"])

    def test_review_status_keeps_only_verifiable_attribution(self):
        self.review["judgments"][0].update(status="needs_review", evidence_quote="不在原文中")
        result = semantic.score(self.packet, self.review)["judgments"][0]
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("引文无法定位", result["reason"])

    def test_changed_packet_and_duplicate(self):
        with self.assertRaises(ValueError):
            semantic.score(self.packet, {**self.review, "packet_sha256": "old"})
        self.review["judgments"] *= 2
        with self.assertRaises(ValueError):
            self.statuses()

    def test_replay_does_not_erase_a_structural_failure(self):
        (self.run / "nodes.jsonl").write_text("\n".join(map(json.dumps, self.nodes)), encoding="utf-8")
        (self.run / "semantic_review.json").write_text(json.dumps(self.review), encoding="utf-8")
        result = evaluate_archive(self.run, {"workflow_status": "succeeded"}, self.expected)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["evidence"]["semantic"]["reviewer"], "fixture")
        (self.run / "semantic_review.json").unlink()
        result = evaluate_archive(self.run, {"workflow_status": "succeeded"}, self.expected)
        self.assertTrue(any(f["code"] == "semantic_review" and f["status"] == "needs_review"
                            for f in result["findings"]))

    def test_judge_reports_counterevidence(self):
        self.review["judgments"][1].update(status="fail", reason="引文实测为 4.5，候选写成了 5.4。",
                                            claim_quote="T1 报告试样 A 的密度为 5.4。")
        self.nodes[0]["outputs"]["candidate_list"][0]["design_rationale"] = self.review["judgments"][1]["claim_quote"]
        self.packet = semantic.packet_for(self.run, self.nodes, self.expected)
        self.review["packet_sha256"] = self.packet["packet_sha256"]
        self.assertEqual(self.statuses(), ["needs_review", "fail"])

    def test_invalid_opt_in(self):
        with self.assertRaises(ValueError):
            validate_expected({**self.expected, "semantic_review": "false"}, "case")
