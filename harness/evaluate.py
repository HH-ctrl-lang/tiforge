"""Independently evaluate one archived TiForge run against a fixed case."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from .engine import common, v00, v01
from .engine.common import load_run, node_fields, read_rows

EVALUATION_VERSION = "2"
FAILURE_TYPES = {
    "hard_constraint_violation",
    "out_of_domain",
    "prediction_uncertainty",
    "insufficient_evidence",
    "tool_error",
    "insufficient_budget",
}


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


def evaluate_run(run, manifest, expected, engine="v01"):
    if manifest.get("capture_error"):
        return "needs_review", ["归档采集失败或不完整。"]
    workflow_status = manifest.get("workflow_status")
    workflow_status = "succeeded" if workflow_status == "success" else workflow_status
    if not isinstance(workflow_status, str) or not workflow_status.strip():
        return "needs_review", ["缺少工作流最终状态。"]
    if expected["route"] != "failure_handling":
        if engine == "v00":
            return v00.evaluate_run(run, manifest, expected)
        result = v01.evaluate(run, manifest, expected)
        return result["status"], result["findings"]

    expected_status = expected.get("workflow_status", "succeeded")
    if workflow_status != expected_status:
        if expected_status == "failed":
            return "fail", ["预期按规则停止，工作流最终状态不符。"]
        return "fail", ["工作流未成功完成。"]

    try:
        nodes = list(read_rows(run / "nodes.jsonl"))
    except (OSError, ValueError) as exc:
        return "needs_review", [f"无法读取节点记录：{exc}"]
    if not nodes:
        return "needs_review", ["归档没有节点记录。"]
    return evaluate_failure_handling(nodes, expected["checks"])


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
