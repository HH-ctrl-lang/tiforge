"""Run one TiForge query or a JSONL suite and archive each attempt."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from uuid import uuid4

from scripts.archive_runtime_run import archive_run, create_run_dir, save_json
from scripts.runtime_api import DEFAULT_QUERY, capture_events, read_credentials


BASE_DIR = Path(__file__).resolve().parent


def load_cases(path):
    cases = []
    seen = set()
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} 第 {number} 行不是有效的 JSON。") from exc
        if not isinstance(case, dict):
            raise ValueError(f"{path} 第 {number} 行必须是 JSON 对象。")
        case_id, inputs = case.get("case_id"), case.get("inputs")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"{path} 第 {number} 行缺少有效的 case_id。")
        if not isinstance(inputs, dict) or not inputs:
            raise ValueError(f"{path} 第 {number} 行缺少有效的 inputs 对象。")
        if case_id in seen:
            raise ValueError(f"{path} 包含重复的 case_id：{case_id}")
        seen.add(case_id)
        cases.append((case_id, inputs))
    if not cases:
        raise ValueError(f"{path} 没有测试用例。")
    return cases


def execute_case(url, key, archive_dir, config, case_id, inputs):
    run_dir = create_run_dir(archive_dir)
    metadata = {
        **config,
        "case_id": case_id,
        "session_id": str(uuid4()),
        "request_id": str(uuid4()),
        "requested_at": datetime.now().astimezone().isoformat(),
    }
    save_json(run_dir / "request.json", {**metadata, "inputs": inputs})
    print(f"开始运行 {case_id}，Run ID：{run_dir.name}")
    capture_error = capture_events(
        url, key, inputs, run_dir / "raw_events.jsonl",
        metadata["session_id"], metadata["request_id"],
    )
    manifest = archive_run(run_dir, {**metadata, "input_file": "request.json"}, capture_error)

    print(f"归档目录：{run_dir.resolve()}")
    print(
        f"工作流状态：{manifest['workflow_status']}，"
        f"诊断状态：{manifest['diagnostic_status']}，"
        f"事件：{manifest['event_count']}，节点：{manifest['node_count']}，"
        f"失败记录：{manifest['failure_count']}，科研结果：{manifest['scientific_outcome_count']}"
    )
    if capture_error:
        print(f"采集异常：{capture_error['kind']}")
    return manifest["diagnostic_status"] == "normal", capture_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cases_input = parser.add_mutually_exclusive_group()
    cases_input.add_argument("--query", help="单次运行使用的自然语言输入")
    cases_input.add_argument("--cases", type=Path, help="包含 case_id 和 inputs 的 JSONL 用例文件")
    parser.add_argument("--case-id", help="单次运行的用例标识")
    parser.add_argument("--workflow-version", help="覆盖 config/config.json 中的工作流版本")
    parser.add_argument("--harness-version", help="覆盖 config/config.json 中的 Harness 版本")
    parser.add_argument("--archive-dir", type=Path, default=BASE_DIR / "archive")
    args = parser.parse_args()
    if args.cases and args.case_id:
        parser.error("--case-id 仅适用于单次运行。")

    try:
        config = json.loads((BASE_DIR / "config" / "config.json").read_text(encoding="utf-8-sig"))
        for name in ("workflow_version", "harness_version"):
            value = getattr(args, name) or config.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"请在 config/config.json 或命令行中填写 {name}。")
            config[name] = value.strip()
        config = {
            name: config[name] for name in (
                "project_name", "task_system", "log_schema_version",
                "workflow_version", "harness_version",
            ) if name in config
        }
        if args.cases:
            cases = load_cases(args.cases)
        else:
            query = DEFAULT_QUERY if args.query is None else args.query
            if not query.strip():
                raise ValueError("--query 不能为空。")
            default_id = "smoke-alloy" if args.query is None else "ad-hoc"
            cases = [(args.case_id or default_id, {"query": query})]
        print(f"记录版本：Workflow {config['workflow_version']}，Harness {config['harness_version']}")
        url, key = read_credentials()
    except (OSError, ValueError, EOFError) as exc:
        parser.exit(2, f"无法开始测试：{exc}\n")

    all_passed = True
    for case_id, inputs in cases:
        passed, capture_error = execute_case(url, key, args.archive_dir, config, case_id, inputs)
        all_passed = all_passed and passed
        if capture_error and capture_error["kind"] == "interrupted":
            return 130
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
