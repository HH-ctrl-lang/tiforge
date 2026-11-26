"""Compare two TiForge Runtime archive batches against one regression suite."""

import argparse
from hashlib import sha256
import json
from pathlib import Path


from .common import check_composition, load_batch, load_cases, node_fields, read_rows


def evaluate_run(run, manifest, expected):
    if manifest.get("capture_error"):
        return "needs_review", ["归档采集失败或不完整。"]
    if manifest.get("workflow_status") not in ("succeeded", "success"):
        return "fail", ["工作流未成功完成。"]

    nodes = list(read_rows(run / "nodes.jsonl"))
    if not nodes:
        return "needs_review", ["归档没有节点记录。"]
    requirements = node_fields(nodes, "研究需求解析")
    if expected["route"] == "clarification":
        questions = requirements.get("clarification_questions")
        reply = node_fields(nodes, "结束", raw=True).get("responseContent")
        if (
            requirements.get("clarification_needed") is True
            and isinstance(questions, list)
            and isinstance(reply, str)
            and any(
                isinstance(question, str) and question.strip() and question.strip() in reply
                for question in questions
            )
            and not any(node.get("node_name") == "候选材料设计" for node in nodes)
        ):
            return "pass", []
        return "fail", ["应提出澄清问题并跳过候选设计。"]

    design = node_fields(nodes, "候选材料设计")
    candidates = design.get("candidate_list")
    if requirements.get("clarification_needed") is not False:
        return "fail", ["预期继续设计，实际未确认进入设计分支。"]
    if not isinstance(candidates, list) or len(candidates) != expected["candidate_count"]:
        return "fail", ["候选数量不符合用例预期。"]

    allowed = set(expected["allowed_elements"])
    candidate_ids = set()
    for entry in candidates:
        candidate_id = entry.get("candidate_id") if isinstance(entry, dict) else None
        if not isinstance(candidate_id, str) or not candidate_id.strip() or candidate_id in candidate_ids:
            return "fail", ["候选 ID 缺失或重复。"]
        candidate_ids.add(candidate_id)
        if not check_composition(entry.get("composition_wt_pct"), allowed):
            return "fail", [f"{candidate_id} 的成分格式、元素或总量不符合用例预期。"]

    check = node_fields(nodes, "材料成分约束检查")
    checked = check.get("checked_candidates")
    if not isinstance(checked, list) or len(checked) != len(candidate_ids):
        return "fail", ["成分检查记录与候选数量不一致。"]
    checked_ids = {
        row["candidate_id"] for row in checked
        if isinstance(row, dict) and isinstance(row.get("candidate_id"), str) and row.get("passed") is True
    }
    if check.get("all_passed") is not True or checked_ids != candidate_ids:
        return "fail", ["成分守门未通过或检查结果与候选 ID 不一致。"]
    return "pass", []


def compare(cases, baseline_dir, candidate_dir, target_case, change):
    if target_case not in cases or not change.strip():
        raise ValueError("需要提供用例集中的目标用例以及改动说明。")
    baseline, baseline_version = load_batch(baseline_dir, cases)
    candidate, candidate_version = load_batch(candidate_dir, cases)
    baseline_ids = {run.name for run, _ in baseline.values()}
    candidate_ids = {run.name for run, _ in candidate.values()}
    if baseline_version == candidate_version or baseline_ids & candidate_ids:
        raise ValueError("基线和候选需要不同的工作流版本和各自独立的 Run。")

    results = {}
    for case_id, case in cases.items():
        pair = {}
        for name, batch in (("baseline", baseline), ("candidate", candidate)):
            run, manifest = batch[case_id]
            status, issues = evaluate_run(run, manifest, case["expected"])
            pair[name] = {"run": str(run), "status": status, "issues": issues}
        results[case_id] = pair

    regressions = [
        case_id for case_id, pair in results.items()
        if pair["baseline"]["status"] == "pass" and pair["candidate"]["status"] == "fail"
    ]
    failed_candidates = [
        case_id for case_id, pair in results.items() if pair["candidate"]["status"] == "fail"
    ]
    target = results[target_case]
    if failed_candidates:
        decision, reason = "reject", "候选未通过固定用例。"
    elif any(result["status"] == "needs_review" for pair in results.values() for result in pair.values()):
        decision, reason = "needs_review", "至少一侧的运行证据不完整。"
    elif (target["baseline"]["status"], target["candidate"]["status"]) != ("fail", "pass"):
        decision, reason = "reject", "目标用例未从失败变为通过。"
    else:
        decision, reason = "accept", "目标用例改善，候选通过全部固定用例。"
    return {
        "scope": "structured_regression",
        "decision": decision,
        "reason": reason,
        "change": change,
        "target_case_id": target_case,
        "baseline_version": baseline_version,
        "candidate_version": candidate_version,
        "regressions": regressions,
        "cases": results,
    }


def configure(parser):
    parser.add_argument("--cases", type=Path, default=Path(__file__).resolve().parents[1] / "cases" / "v00.jsonl",
                        help="固定回归用例 JSONL")
    parser.add_argument("--baseline", type=Path, required=True, help="基线版本的批量归档目录")
    parser.add_argument("--candidate", type=Path, required=True, help="候选版本的批量归档目录")
    parser.add_argument("--target-case", "--target", dest="target", required=True, help="此次改动针对的用例 ID")
    parser.add_argument("--change", required=True, help="改动说明或对应的变更记录")
    parser.set_defaults(run=run)


def run(args, parser):
    try:
        cases = load_cases(args.cases)
        report = compare(cases, args.baseline, args.candidate, args.target, args.change)
        report["suite_sha256"] = sha256(json.dumps(cases, sort_keys=True).encode("utf-8")).hexdigest()
    except (OSError, ValueError) as exc:
        parser.exit(2, f"无法比较归档：{exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {"accept": 0, "reject": 1, "needs_review": 2}[report["decision"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    configure(parser)
    return run(parser.parse_args(), parser)


if __name__ == "__main__":
    raise SystemExit(main())
