"""Audit TiForge development runs and compare revisions against the same evidence checks."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from .common import check_composition, load_batch, load_cases, node_fields, read_rows


LITERATURE = "文献知识提取"
DESIGN = "候选材料设计"
GATE = "材料成分约束检查"
REVIEW = "候选方案审查"
FORMAT_REPLY = "整理输出结果"
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
GATE_OUTCOMES = {"pass": "gate_passed", "reject": "gate_rejected", "needs_review": "gate_pending"}


def by_candidate(rows):
    if not isinstance(rows, list):
        return None
    result = {}
    for row in rows:
        candidate_id = row.get("candidate_id") if isinstance(row, dict) else None
        if not isinstance(candidate_id, str) or not candidate_id.strip() or candidate_id in result:
            return None
        result[candidate_id] = row
    return result


def node_inputs(nodes, name):
    inputs = nodes.get(name, {}).get("inputs")
    if isinstance(inputs, dict):
        inputs = inputs.get("userFields", inputs)
    return inputs if isinstance(inputs, dict) else {}


def evaluate(run, manifest, expected):
    evidence = {"run_id": run.name}
    findings = []

    def note(code, node, detail, status="fail"):
        findings.append({"code": code, "node": node, "detail": detail, "status": status})

    def result():
        states = {item["status"] for item in findings}
        status = "fail" if "fail" in states else "needs_review" if states else "pass"
        return {"status": status, "findings": findings, "evidence": evidence}

    failure_file = run / "failure_records.jsonl"
    if failure_file.is_file():
        evidence["runtime_issues"] = [
            {key: row.get(key) for key in ("node_name", "failure_type", "error_code")}
            for row in read_rows(failure_file)
        ]
    if manifest.get("capture_error"):
        note("capture_error", None, manifest["capture_error"], "needs_review")
        return result()
    if manifest.get("workflow_status") not in {"success", "succeeded"}:
        evidence["failure_nodes"] = [
            row["node_name"] for row in evidence.get("runtime_issues", [])
        ]
        note("workflow_failure", None, manifest.get("workflow_status"))
        return result()

    nodes = list(read_rows(run / "nodes.jsonl"))
    if not nodes:
        note("missing_nodes", None, "归档没有节点记录。", "needs_review")
        return result()
    executed = {row.get("node_name"): row for row in nodes}
    evidence["runtime_nodes"] = list(executed)
    evidence["node_duration_ms"] = {
        name: row["duration_ms"] for name, row in executed.items() if isinstance(row.get("duration_ms"), int)
    }
    requirements = node_fields(nodes, "研究需求解析")
    reply = node_fields(nodes, "结束", raw=True).get("responseContent")
    evidence["clarification_needed"] = requirements.get("clarification_needed")
    if expected["route"] == "clarification":
        questions = requirements.get("clarification_questions")
        if (requirements.get("clarification_needed") is not True
                or set(executed) & {LITERATURE, DESIGN, GATE, REVIEW}
                or not isinstance(reply, str) or not isinstance(questions, list)
                or not any(isinstance(q, str) and q.strip() and q in reply for q in questions)):
            note("clarification_route", "研究需求解析", "应回传澄清问题并停止后续设计链路。")
        return result()

    if requirements.get("clarification_needed") is not False:
        note("clarification_route", "研究需求解析", "用例预期继续设计。")
        return result()

    literature = node_fields(nodes, LITERATURE)
    records = literature.get("records")
    status = literature.get("status")
    query = requirements.get("literature_query")
    target = expected.get("literature", {})
    record_id = target.get("record_id")
    evidence["literature"] = {
        "query": query, "status": status, "call_id": literature.get("call_id"),
        "tool_version": literature.get("tool_version"), "dataset_version": literature.get("dataset_version"),
        "elapsed_ms": literature.get("elapsed_ms"),
        "record_ids": [row.get("record_id") for row in records if isinstance(row, dict)]
        if isinstance(records, list) else None,
    }
    if (LITERATURE not in executed or not isinstance(query, str) or not query.strip()
            or not isinstance(records, list) or status not in ("success", "needs_review", "no_evidence")):
        note("literature_lookup", LITERATURE, {"status": status, "error_code": literature.get("error_code")})
    else:
        if record_id and record_id.casefold() not in query.casefold():
            note("literature_scope", "研究需求解析", f"查询丢失指定记录 {record_id}。")
        if target.get("status", status) != status:
            note("literature_scope", LITERATURE, f"文献状态 {status}，预期 {target['status']}。")
        if status == "no_evidence":
            gaps = literature.get("missing_evidence")
            if records or not isinstance(gaps, list) or not gaps or (
                record_id and not any(isinstance(gap, str) and record_id.casefold() in gap.casefold() for gap in gaps)
            ):
                note("literature_scope", LITERATURE, "无证据结果应返回空记录和对应的证据缺口。")
        elif target.get("source_doi") and not any(
            isinstance(row, dict) and row.get("record_id") == record_id
            and row.get("source_doi") == target["source_doi"] and row.get("review_required") is True
            for row in records
        ):
            note("literature_scope", LITERATURE, "缺少指定记录的 DOI 或待复核标记。")

        handoff = {f"literature_{key}": literature.get(key) for key in ("status", "records", "missing_evidence")}
        for name in (DESIGN, REVIEW):
            inputs = node_inputs(executed, name)
            if not handoff.keys() <= inputs.keys():
                note("literature_handoff", name, "归档缺少下游文献状态、记录或缺口字段。", "needs_review")
            elif any(inputs[key] != value for key, value in handoff.items()):
                note("literature_handoff", name, "收到的文献状态、记录或缺口与工具输出不一致。")

    candidates = by_candidate(node_fields(nodes, DESIGN).get("candidate_list"))
    if not candidates or len(candidates) != expected["candidate_count"]:
        note("candidate_composition", DESIGN, "候选数量或 ID 不符合预期。")
        return result()
    allowed = set(expected["allowed_elements"])
    evidence["compositions"] = {key: row.get("composition_wt_pct") for key, row in candidates.items()}
    invalid = [key for key, value in evidence["compositions"].items() if not check_composition(value, allowed)]
    if invalid:
        note("candidate_composition", DESIGN, {"invalid_candidate_ids": invalid})

    gate = node_fields(nodes, GATE)
    decisions = by_candidate(gate.get("candidate_decisions"))
    checks = gate.get("physical_checks")
    if decisions is None or decisions.keys() != candidates.keys():
        note("gate_alignment", GATE, "逐候选判定与候选 ID 无法完整对应。")
    else:
        states = {key: row.get("status") for key, row in decisions.items()}
        evidence["gate_states"] = states
        evidence["all_passed"] = gate.get("all_passed")
        evidence["gate_version"] = gate.get("version")
        evidence["gate_scenario"] = gate.get("scenario_id")
        rejection = [key for key, state in states.items() if state == "reject"]
        if rejection:
            note("gate_rejected", GATE, {"candidate_ids": rejection})
        valid_states = all(isinstance(state, str) and state in GATE_OUTCOMES for state in states.values())
        if not valid_states or gate.get("all_passed") is not all(state == "pass" for state in states.values()):
            note("gate_alignment", GATE, "逐候选状态与汇总结果不一致或缺失。")
        if valid_states:
            outcomes_path = run / "scientific_outcomes.jsonl"
            if outcomes_path.is_file():
                archived = sorted((str(row.get("candidate_id")), row["outcome_type"])
                                  for row in read_rows(outcomes_path)
                                  if row.get("outcome_type") in GATE_OUTCOMES.values())
                evidence["archived_gate_outcomes"] = archived
                if archived != sorted((key, GATE_OUTCOMES[state]) for key, state in states.items()):
                    note("archive_gate_mapping", GATE, "科研结果归档与逐候选守门状态不一致。")
            else:
                note("archive_gate_mapping", GATE, "缺少科研结果归档。", "needs_review")
        for name in (REVIEW, FORMAT_REPLY):
            received = by_candidate(node_inputs(executed, name).get("candidate_decisions"))
            if received is None:
                note("review_alignment", name, "归档缺少下游的逐候选守门判定。", "needs_review")
            elif {key: row.get("status") for key, row in received.items()} != states:
                note("review_alignment", name, "收到的逐候选守门状态与工具输出不一致。")
        review = node_fields(nodes, REVIEW)
        evidence["review_status"] = review.get("review_status")
        reviews = by_candidate(review.get("candidate_reviews"))
        if reviews is None or reviews.keys() != candidates.keys():
            note("review_alignment", REVIEW, "审查和候选 ID 无法完整对应。")
        if not isinstance(reply, str) or any(key not in reply for key in candidates):
            note("review_alignment", "结束", "最终回复缺少候选编号。")

    if isinstance(checks, list):
        evidence["physical_checks"] = [
            {key: row.get(key) for key in ("candidate_id", "rule_id", "status")}
            for row in checks if isinstance(row, dict)
        ]
        composition_checks = by_candidate([
            row for row in checks if isinstance(row, dict) and row.get("rule_id") == "COMPOSITION"
        ])
        if not composition_checks or composition_checks.keys() != candidates.keys() or any(
            row.get("status") != "pass" for row in composition_checks.values()
        ):
            note("gate_alignment", GATE, "基础成分规则缺失或未通过。")
    else:
        note("gate_alignment", GATE, "缺少逐规则检查结果。", "needs_review")
    return result()


def audit(cases, directory):
    batch, version = load_batch(directory, cases)
    results = {}
    proposals = {}
    for case_id, (run, manifest) in batch.items():
        try:
            result = evaluate(run, manifest, cases[case_id]["expected"])
        except (OSError, ValueError) as exc:
            result = {"status": "needs_review", "evidence": {"run_id": run.name}, "findings": [
                {"code": "unreadable_evidence", "node": None, "detail": str(exc), "status": "needs_review"}
            ]}
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
    statuses = {case["status"] for case in candidate["cases"].values()}
    if "fail" in statuses:
        decision = "reject"
    elif "needs_review" in statuses:
        decision = "needs_review"
    else:
        decision = "ready_for_full_regression"
    return {
        "decision": decision, "target_case": target_case, "target_finding": target_finding,
        "baseline": baseline, "candidate": candidate,
    }


def configure(parser):
    parser.add_argument("--cases", type=Path, default=Path(__file__).resolve().parents[1] / "cases" / "v01.jsonl")
    parser.add_argument("--baseline", "--archive", dest="archive", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, help="可选的独立候选版本归档")
    parser.add_argument("--target-case", help="对照时指定目标用例")
    parser.add_argument("--target-finding", help="对照时指定基线报告中的目标问题代码")
    parser.set_defaults(run=run)


def run(args, parser):
    comparison_args = (args.candidate, args.target_case, args.target_finding)
    if any(comparison_args) and not all(comparison_args):
        parser.error("对照时需要同时提供 --candidate、--target-case 和 --target-finding。")
    try:
        cases = load_cases(args.cases)
        report = audit(cases, args.archive)
        if args.candidate:
            report = compare(report, audit(cases, args.candidate), args.target_case, args.target_finding)
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
