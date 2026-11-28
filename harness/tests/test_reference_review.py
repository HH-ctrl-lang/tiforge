from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness import semantic
from harness.core.evaluate import evaluate_archive, validate_expected
from harness.core.reference_review import citation_claims, composition_claims, record_check


class ReferenceReviewTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run = Path(tmp.name)
        self.record = {"record_id": "Ti001", "state": "铸态", "E_GPa": 54.34, "density_g_cm3": None,
                       "source_loc": "Table 2 S-1", "source_doi": "sample-doi", "review_required": True}
        self.lit = {"status": "needs_review", "records": [self.record], "missing_evidence": ["缺少目标态记录"]}
        self.nodes = [{"node_name": "文献知识提取", "outputs": self.lit},
                      {"node_name": "结束", "outputs": {"responseContent": "缺少目标态依据，保留待验证。"}}]
        self.expected = {"route": "reference_review", "scope": "answer", "criterion": "说明目标状态的证据缺口。"}

    def evaluate(self, status="succeeded"):
        (self.run / "nodes.jsonl").write_text("\n".join(map(json.dumps, self.nodes)), encoding="utf-8")
        return evaluate_archive(self.run, {"workflow_status": status}, self.expected)

    def test_case_validation(self):
        with self.assertRaises(ValueError):
            validate_expected({**self.expected, "scope": "unknown"}, "a")

    def test_real_answer_must_match_its_quoted_source_and_hash(self):
        packet = semantic.packet_for(self.run, self.nodes, self.expected)
        review = {"packet_sha256": packet["packet_sha256"], "reviewer": "test", "judgments": [
            {"candidate_id": "answer", "criterion": "evidence_boundary", "status": "pass", "reason": "保留缺口",
             "claim_field": "answer", "claim_quote": "保留待验证", "source": "retrieval_gap/0", "evidence_quote": "缺少目标态记录"}]}
        (self.run / "semantic_review.json").write_text(json.dumps(review), encoding="utf-8")
        self.assertEqual(self.evaluate()["status"], "pass")
        self.nodes[-1]["outputs"]["responseContent"] += "已测得目标态性能。"
        self.assertEqual(self.evaluate()["status"], "needs_review")

    def test_partial_record_is_not_no_evidence(self):
        fields = {"record_id": "Ti001", "density_g_cm3": None, "E_GPa": 54.34}
        self.assertEqual(record_check(self.lit, fields)[0], "pass")
        self.assertEqual(record_check({"status": "no_evidence", "records": []}, fields)[0], "fail")
        del self.record["density_g_cm3"]
        self.assertEqual(record_check(self.lit, fields)[0], "fail")
        self.assertEqual(record_check({}, fields)[0], "needs_review")

    def test_optional_comparator_and_missing_review_handoff(self):
        self.assertEqual(record_check({"status":"no_evidence", "records":[]}, {"record_id":"T1"}, allow_empty=True)[0], "pass")
        self.expected["require_review_summary"] = True
        self.assertEqual(self.evaluate()["findings"][0]["code"], "missing_review_subject")

    def test_failed_workflow_and_component_have_different_scope(self):
        self.nodes.pop()
        self.assertEqual(self.evaluate("failed")["findings"][0]["code"], "workflow_failure")
        self.expected["scope"] = "literature"
        self.assertEqual(self.evaluate("failed")["status"], "needs_review")

    def test_process_mismatch_numeric_mismatch_and_units(self):
        def check(sentence):
            return citation_claims(sentence, self.lit)[0]["status"]
        self.assertEqual(check("据 Ti001，铸态弹性模量为 54.34 GPa。"), "pass")
        self.assertEqual(check("据 Ti001，铸态弹性模量为 54340 MPa。"), "pass")
        self.assertEqual(check("据 Ti001，铸态弹性模量为 55 GPa。"), "fail")
        self.assertEqual(check("据 Ti001，固溶＋时效态弹性模量为 54.34 GPa。"), "fail")
        self.assertEqual(check("据 Ti01，铸态弹性模量为 54.34 GPa。"), "needs_review")
        self.assertEqual(citation_claims("不能说据 Ti001，铸态弹性模量为 55 GPa。", self.lit), [])
        self.record["state"] = None
        self.assertEqual(check("据 Ti001，铸态弹性模量为 54.34 GPa。"), "needs_review")

    def test_composition_conflict_overrides_favourable_review(self):
        expected = {"route": "design", "candidate_count": 1, "allowed_elements": ["Ti","V"], "semantic_review": True}
        nodes = deepcopy(self.nodes)
        rationale = "本方案把 V 降至 2 wt.%。据 Ti001，时效态弹性模量为 54.34 GPa。"
        nodes.append({"node_name": "候选材料设计", "outputs": {"candidate_list": [
            {"candidate_id": "C01", "composition_wt_pct": "Ti:99,V:1", "design_rationale": rationale}]}})
        packet = semantic.packet_for(self.run, nodes, expected)
        review = {"packet_sha256": packet["packet_sha256"], "reviewer": "test", "judgments": [
            {"candidate_id": "C01", "criterion": key, "status": "pass", "reason": "看似一致",
             "claim_field": "design_rationale", "claim_quote": rationale, "source":"literature/0/E_GPa",
             "evidence_quote":"54.34"} for key in semantic.RUBRIC]}
        (self.run / "semantic_review.json").write_text(json.dumps(review), encoding="utf-8")
        self.nodes, self.expected = nodes, expected
        result = self.evaluate()
        self.assertEqual([r["status"] for r in result["evidence"]["semantic"]["judgments"]], ["fail", "fail"])
        self.assertEqual(composition_claims("若将 V 降至 2 wt.%", "V:1,Ti:99"), [])
