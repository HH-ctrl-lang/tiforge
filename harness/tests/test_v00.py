import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from harness.engine.common import load_cases
from harness.engine.v00 import compare


class RegressionTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.cases = {
            "clarify": {
                "inputs": {"query": "加入 Nb，同时禁止 Nb。"},
                "expected": {"route": "clarification"},
            },
            "design": {
                "inputs": {"query": "设计一个 Ti-Nb 合金。"},
                "expected": {"route": "design", "candidate_count": 1, "allowed_elements": ["Ti", "Nb"]},
            },
        }

    def write_run(self, side, case_id, *, valid=True, capture_error=None):
        run = self.root / side / f"tf-{side}-{case_id}"
        run.mkdir(parents=True, exist_ok=True)
        meta = {
            "case_id": case_id,
            "workflow_version": side,
            "run_id": run.name,
            "workflow_status": "succeeded",
            "capture_error": capture_error,
        }
        (run / "manifest.json").write_text(json.dumps(meta), encoding="utf-8")
        request = {**meta, "inputs": self.cases[case_id]["inputs"]}
        (run / "request.json").write_text(json.dumps(request), encoding="utf-8")
        question = "Nb 是必需还是禁止？"
        nodes = [{
            "node_name": "研究需求解析",
            "outputs": {"clarification_needed": case_id == "clarify", "clarification_questions": [question]},
        }]
        if case_id == "clarify":
            nodes.append({"node_name": "结束", "outputs": {"responseContent": question if valid else ""}})
        else:
            composition = "Ti:80,Nb:20" if valid else "Ti:80,V:20"
            nodes += [
                {"node_name": "候选材料设计", "outputs": {"candidate_list": [
                    {"candidate_id": "C01", "composition_wt_pct": composition},
                ]}},
                {"node_name": "材料成分约束检查", "outputs": {"userFields": {
                    "all_passed": True, "checked_candidates": [{"candidate_id": "C01", "passed": True}],
                }}},
            ]
        (run / "nodes.jsonl").write_text("\n".join(map(json.dumps, nodes)), encoding="utf-8")
        return run, meta

    def compare_pair(self):
        return compare(self.cases, self.root / "base", self.root / "candidate", "clarify", "修复澄清输出")

    def set_pair(self, *, target_improved=True, guard_valid=True, capture_error=None):
        self.write_run("base", "clarify", valid=not target_improved)
        self.write_run("base", "design")
        self.write_run("candidate", "clarify")
        self.write_run("candidate", "design", valid=guard_valid, capture_error=capture_error)

    def test_accepts_improvement_without_regression(self):
        self.set_pair()
        report = self.compare_pair()
        self.assertEqual(report["decision"], "accept")
        self.assertEqual(report["cases"]["clarify"]["baseline"]["status"], "fail")
        self.assertEqual(report["cases"]["clarify"]["candidate"]["status"], "pass")

    def test_rejects_guard_regression(self):
        self.set_pair(guard_valid=False)
        report = self.compare_pair()
        self.assertEqual(report["decision"], "reject")
        self.assertEqual(report["regressions"], ["design"])

    def test_rejects_without_target_improvement(self):
        self.set_pair(target_improved=False)
        self.assertEqual(self.compare_pair()["decision"], "reject")

    def test_capture_error_requires_review(self):
        self.set_pair(capture_error={"kind": "timeout"})
        self.assertEqual(self.compare_pair()["decision"], "needs_review")

    def test_rejects_pair_with_mismatched_inputs(self):
        self.set_pair()
        run = self.root / "candidate" / "tf-candidate-design" / "request.json"
        request = json.loads(run.read_text(encoding="utf-8"))
        request["inputs"] = {"query": "不同输入"}
        run.write_text(json.dumps(request), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "实际输入"):
            self.compare_pair()

    def test_project_suite_has_three_cases(self):
        cases = load_cases(Path(__file__).resolve().parents[1] / "cases" / "v00.jsonl")
        self.assertEqual(set(cases), {
            "biomedical-low-modulus-baseline", "industrial-cost-constraint", "nb-conflict-01",
        })


if __name__ == "__main__":
    unittest.main()
