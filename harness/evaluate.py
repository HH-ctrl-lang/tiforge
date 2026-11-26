"""Independently evaluate one archived TiForge run against a fixed case."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from .engine import common
from .engine.common import check_composition, load_run, node_fields, read_rows

EVALUATION_VERSION = "2"
FAILURE_TYPES = {
    "hard_constraint_violation",
    "out_of_domain",
    "prediction_uncertainty",
    "insufficient_evidence",
    "tool_error",
    "insufficient_budget",
}

LITERATURE = "文献知识提取"
DESIGN = "候选材料设计"
GATE = "材料成分约束检查"
REVIEW = "候选方案审查"
FORMAT_REPLY = "整理输出结果"
GATE_OUTCOMES = {"pass": "gate_passed", "reject": "gate_rejected", "needs_review": "gate_pending"}

def validate_expected(expected, case_id):
    if expected.get("route") != "failure_handling":
        return common.validate_expected(expected, case_id)
    validate_failure_checks(expected.get("checks"), case_id)
    if expected.get("workflow_status", "succeeded") not in ("succeeded", "failed"):
        raise ValueError(f"{case_id} 的 workflow_status 必须是 succeeded 或 failed。")


def load_cases(path):
    return common.load_cases(path, validate=validate_expected)


def validate_failure_checks(checks, case_id):
    if not isinstance(checks, list) or not checks:
        raise ValueError(f"{case_id} 必须提供非空的 checks 数组。")
    for check in checks:
        failure_type = check.get("type") if isinstance(check, dict) else None
        if not isinstance(failure_type, str) or failure_type not in FAILURE_TYPES:
            raise ValueError(f"{case_id} 有未知的失败类型：{failure_type!r}。")
        for key in ("node", "field"):
            value = check.get(key, "")
            if not isinstance(value, str) or (key == "node" and not value.strip()):
                raise ValueError(f"{case_id} 的 {key} 必须为字符串，节点名不能为空。")
        if check.get("source", "outputs") not in ("outputs", "error"):
            raise ValueError(f"{case_id} 的 source 必须是 outputs 或 error。")
        where = check.get("where", {})
        equals = check.get("equals")
        required_text = check.get("required_text", [])
        if not isinstance(where, dict) or (where and not check.get("field")):
            raise ValueError(f"{case_id} 的 where 必须是对象，使用时须指定 field。")
        if not isinstance(equals, dict) or not equals:
            raise ValueError(f"{case_id} 的 equals 必须是非空对象。")
        if not isinstance(required_text, list) or not all(
            isinstance(key, str) and key.strip() for key in required_text
        ):
            raise ValueError(f"{case_id} 的 required_text 必须是字段名数组。")


def matches_fields(record, expected):
    return all(
        key in record and type(record[key]) is type(value) and record[key] == value
        for key, value in expected.items()
    )


def evaluate_failure_handling(nodes, checks):
    failures, missing = [], []
    for check in checks:
        node = check["node"]
        field = check.get("field")
        reference = f"{node}.{field}" if field else node
        selector = check.get("where", {})
        label = f"{check['type']}（{reference}）"
        if selector:
            label += f" {json.dumps(selector, ensure_ascii=False, sort_keys=True)}"
        try:
            source = check.get("source", "outputs")
            fields = node_fields(nodes, node, raw=source == "error", source=source)
        except ValueError as exc:
            missing.append(f"{label}：{exc}")
            continue
        evidence = fields.get(field) if field else fields
        if selector and isinstance(evidence, list):
            records = [
                row for row in evidence if isinstance(row, dict) and matches_fields(row, selector)
            ]
            evidence = records[0] if len(records) == 1 else None
        if not isinstance(evidence, dict) or not evidence or (
            selector and not matches_fields(evidence, selector)
        ):
            missing.append(f"{label}：缺少唯一可核对的输出记录。")
            continue
        for key, value in check["equals"].items():
            if key not in evidence:
                missing.append(f"{label}：缺少 {key}。")
            elif not matches_fields(evidence, {key: value}):
                failures.append(f"{label}：{key} 与固定预期不符。")
        for key in check.get("required_text", []):
            value = evidence.get(key)
            if not isinstance(value, str) or not value.strip():
                missing.append(f"{label}：缺少可读的 {key}。")
    if failures:
        return "fail", failures + missing
    if missing:
        return "needs_review", missing
    return "pass", []


def _evaluate_v00(nodes, expected):
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


def _evaluate_v01(run, nodes, expected, evidence):
    findings = []

    def note(code, node, detail, status="fail"):
        findings.append({"code": code, "node": node, "detail": detail, "status": status})

    def result():
        states = {item["status"] for item in findings}
        status = "fail" if "fail" in states else "needs_review" if states else "pass"
        return {"status": status, "findings": findings, "evidence": evidence}

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


def _report(status, code, details, evidence):
    return {
        "status": status,
        "findings": [
            {"code": code, "node": None, "detail": detail, "status": status}
            for detail in details
        ],
        "evidence": evidence,
    }


def evaluate_archive(run, manifest, expected, engine="v01"):
    """Return one verdict with the structured evidence used by Harness."""
    if engine not in ("v00", "v01"):
        raise ValueError(f"未知的评测版本：{engine!r}。")
    evidence = {"run_id": run.name}
    failure_handling = expected["route"] == "failure_handling"
    use_v01 = engine == "v01" and not failure_handling

    try:
        if use_v01 and (run / "failure_records.jsonl").is_file():
            evidence["runtime_issues"] = [
                {key: row.get(key) for key in ("node_name", "failure_type", "error_code")}
                for row in read_rows(run / "failure_records.jsonl")
            ]
        if manifest.get("capture_error"):
            return _report("needs_review", "capture_error", [manifest["capture_error"]], evidence)

        workflow_status = manifest.get("workflow_status")
        if not isinstance(workflow_status, str) or not workflow_status.strip():
            return _report("needs_review", "missing_workflow_status", ["缺少工作流最终状态。"], evidence)
        actual_status = "succeeded" if workflow_status == "success" else workflow_status
        expected_status = expected.get("workflow_status", "succeeded") if failure_handling else "succeeded"
        if actual_status != expected_status:
            reason = "预期按规则停止，工作流最终状态不符。" if expected_status == "failed" else "工作流未成功完成。"
            if use_v01:
                evidence["failure_nodes"] = [row["node_name"] for row in evidence.get("runtime_issues", [])]
                reason = workflow_status
            return _report("fail", "workflow_failure", [reason], evidence)

        nodes = list(read_rows(run / "nodes.jsonl"))
        if not nodes:
            return _report("needs_review", "missing_nodes", ["归档没有节点记录。"], evidence)
        if failure_handling:
            status, issues = evaluate_failure_handling(nodes, expected["checks"])
            return _report(status, "failure_handling", issues, evidence)
        if engine == "v00":
            status, issues = _evaluate_v00(nodes, expected)
            return _report(status, "structural_check", issues, evidence)
        return _evaluate_v01(run, nodes, expected, evidence)
    except (OSError, ValueError) as exc:
        return _report("needs_review", "unreadable_evidence", [str(exc)], evidence)


def evaluate_run(run, manifest, expected, engine="v01"):
    """Return the status and issues used by the single-run CLI."""
    result = evaluate_archive(run, manifest, expected, engine)
    findings = result["findings"]
    details_only = engine == "v00" or expected["route"] == "failure_handling"
    if findings and findings[0]["code"] == "capture_error":
        return result["status"], ["归档采集失败或不完整。"]
    if details_only or (findings and findings[0]["code"] in ("missing_workflow_status", "unreadable_evidence")):
        return result["status"], [finding["detail"] for finding in findings]
    return result["status"], findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True, help="固定用例 JSONL")
    parser.add_argument("--run", type=Path, required=True, help="要评价的单个 tf-* 归档目录")
    parser.add_argument("--engine", choices=("v00", "v01"), default="v01",
                        help="设计和澄清用例使用的 Harness 规则版本，默认 v01")
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
        manifest = load_run(args.run, cases)
        case_id = manifest["case_id"]
        status, issues = evaluate_run(args.run, manifest, cases[case_id]["expected"], args.engine)
    except (OSError, ValueError) as exc:
        report = {"status": "needs_review", "issues": [str(exc)]}
    else:
        report = {
            "evaluation_version": EVALUATION_VERSION,
            "engine": args.engine,
            "suite_sha256": sha256(json.dumps(cases, sort_keys=True).encode("utf-8")).hexdigest(),
            "run_id": manifest["run_id"],
            "case_id": case_id,
            "workflow_version": manifest["workflow_version"],
            "status": status,
            "issues": issues,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {"pass": 0, "fail": 1, "needs_review": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
