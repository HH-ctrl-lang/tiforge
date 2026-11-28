"""Audit TiForge development runs and compare revisions against the same evidence checks."""

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from ..core.common import load_batch
from ..core.evaluate import DESIGN, FORMAT_REPLY, GATE, LITERATURE, REVIEW, evaluate_archive as evaluate, load_cases


PROPOSALS = {
    "literature_lookup": "核对需求解析 → 文献 MCP 的查询和调用策略。",
    "literature_scope": "为指定记录的查询和回包加入记录 ID 范围校验。",
    "literature_handoff": "修正文献 MCP 到候选设计及审查的结构化字段绑定。",
    "candidate_composition": "复放相同需求，检查候选设计的硬约束提示和结构化输出。",
    "gate_alignment": "按候选 ID 核对候选设计和物理守门之间的接线。",
    "archive_gate_mapping": "核对科研结果归档的守门三态映射。",
    "review_alignment": "用冻结的守门回包复测审查节点，保持逐候选引用一致。",
    "clarification_route": "核对澄清判断到最终回复的输出路径。",
}


def audit(cases, directory, *, auto_judge=False):
    batch, version = load_batch(directory, cases)
    results = {}
    proposals = {}
    for case_id, (run, manifest) in batch.items():
        result = evaluate(run, manifest, cases[case_id]["expected"], auto_judge=auto_judge)
        results[case_id] = result
        for code in {finding["code"] for finding in result["findings"] if finding["status"] == "fail"}:
            if code in PROPOSALS:
                proposals.setdefault(code, []).append({"case_id": case_id, "run_id": run.name})
    return {
        "harness_version": "v01", "workflow_version": version,
        "suite_sha256": sha256(json.dumps(cases, sort_keys=True).encode("utf-8")).hexdigest(),
        "cases": results,
        "proposals": [{"finding": code, "change": PROPOSALS[code], "evidence": evidence}
                      for code, evidence in sorted(proposals.items(), key=lambda item: (-len(item[1]), item[0]))],
    }


def compare(baseline, candidate, target_case, target_finding):
    if baseline["suite_sha256"] != candidate["suite_sha256"]:
        raise ValueError("两批运行必须使用相同的开发用例集。")
    if baseline["workflow_version"] == candidate["workflow_version"]:
        raise ValueError("两批运行必须对应不同的实际工作流部署版本。")
    baseline_ids = {case["evidence"]["run_id"] for case in baseline["cases"].values()}
    candidate_ids = {case["evidence"]["run_id"] for case in candidate["cases"].values()}
    if baseline_ids & candidate_ids:
        raise ValueError("两批归档不能复用同一次运行。")
    before = baseline["cases"].get(target_case)
    after = candidate["cases"].get(target_case)
    if before is None or after is None:
        raise ValueError("目标用例不在开发用例集中。")
    if not any(item["code"] == target_finding and item["status"] == "fail" for item in before["findings"]):
        raise ValueError("基线没有复现指定的目标问题。")
    gate_differences = []
    for case_id, baseline_case in baseline["cases"].items():
        candidate_case = candidate["cases"].get(case_id)
        if candidate_case is None:
            continue
        for field in ("gate_version", "gate_scenario", "gate_profile_version", "gate_profile_sha256"):
            old = baseline_case["evidence"].get(field)
            new = candidate_case["evidence"].get(field)
            if old and new and old != new:
                gate_differences.append({"case_id": case_id, "field": field, "baseline": old, "candidate": new})

    statuses = {case["status"] for case in candidate["cases"].values()}
    if "fail" in statuses:
        decision = "reject"
    elif "needs_review" in statuses or gate_differences:
        decision = "needs_review"
    else:
        decision = "ready_for_full_regression"
    report = {
        "decision": decision, "target_case": target_case, "target_finding": target_finding,
        "baseline": baseline, "candidate": candidate,
    }
    if gate_differences:
        report["gate_environment_differences"] = gate_differences
    return report


def configure(parser):
    parser.add_argument("--cases", type=Path, default=Path(__file__).resolve().parents[1] / "cases" / "v01.jsonl")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--baseline", "--archive", dest="archive", type=Path)
    source.add_argument("--run-case", help="调用智果运行一条用例并立即评测")
    source.add_argument("--run-all", action="store_true", help="调用智果运行全部用例并立即评测")
    parser.add_argument("--workflow-version", help="直接运行时覆盖项目根目录 config.json 的工作流版本")
    parser.add_argument("--candidate", type=Path, help="可选的独立候选版本归档")
    parser.add_argument("--target-case", help="对照时指定目标用例")
    parser.add_argument("--target-finding", help="对照时指定基线报告中的目标问题代码")
    parser.add_argument("--offline", action="store_true", help="仅使用归档中已有的独立评价结果")
    parser.set_defaults(run=run)


def run(args, parser):
    comparison_args = (args.candidate, args.target_case, args.target_finding)
    run_live = args.run_all or args.run_case is not None
    if run_live:
        if any(comparison_args):
            parser.error("--run-case 和 --run-all 仅评估本次运行。")
    elif args.workflow_version is not None:
        parser.error("--workflow-version 仅适用于 --run-case 或 --run-all。")
    if any(comparison_args) and not all(comparison_args):
        parser.error("对照时需要同时提供 --candidate、--target-case 和 --target-finding。")
    try:
        cases = load_cases(args.cases, default_semantic=True)
        archive = args.archive
        if args.run_case is not None:
            if args.run_case not in cases:
                raise ValueError(f"用例文件中找不到 {args.run_case!r}。")
            cases = {args.run_case: cases[args.run_case]}
        if run_live:
            runner = Path(__file__).resolve().parents[2] / "logging" / "run_and_archive.py"
            batch_name = f"harness-v01-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:12]}"
            archive = runner.parent / "archive" / batch_name
            archive.mkdir(parents=True)
            command = [
                sys.executable, "-u", "-B", str(runner), "--cases", str(args.cases),
                "--harness-version", "v01", "--archive-dir", str(archive),
            ]
            if args.run_case is not None:
                command.extend(["--case-id", args.run_case])
            if args.workflow_version is not None:
                command.extend(["--workflow-version", args.workflow_version])
            outcome = subprocess.run(command, stdout=sys.stderr)
            # Logging returns 1 for a diagnosed anomaly. Its archive can still be evaluated.
            if outcome.returncode not in (0, 1):
                return outcome.returncode
        report = audit(cases, archive, auto_judge=not args.offline)
        if run_live:
            report["archive_directory"] = str(archive)
        if args.candidate:
            candidate = audit(cases, args.candidate, auto_judge=not args.offline)
            report = compare(report, candidate, args.target_case, args.target_finding)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"无法评估归档：{exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.candidate:
        return {"ready_for_full_regression": 0, "reject": 1, "needs_review": 2}[report["decision"]]
    statuses = {result["status"] for result in report["cases"].values()}
    return 1 if "fail" in statuses else 2 if "needs_review" in statuses else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    configure(parser)
    return run(parser.parse_args(), parser)


if __name__ == "__main__":
    raise SystemExit(main())
