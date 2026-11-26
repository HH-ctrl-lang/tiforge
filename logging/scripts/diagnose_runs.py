
import argparse
import json
import uuid
from datetime import datetime
from pathlib import Path

from .normalize_runtime_events import archive_reference, iter_run_dirs
from .tool_results import get_tool_result


BASE_DIR = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = BASE_DIR / "archive"
ANALYSIS_DIR = BASE_DIR / "analysis"

FAILURE_FILE = ANALYSIS_DIR / "failure_records.jsonl"
DIAGNOSTIC_FILE = ANALYSIS_DIR / "run_diagnostics.jsonl"

# 明确表示执行失败的状态
FAILED_STATUSES = {
    "failed",
    "failure",
    "error",
    "exception"
}

# 明确表示超时的状态
TIMEOUT_STATUSES = {
    "timeout",
    "timed_out"
}

# 明确表示取消的状态
CANCELLED_STATUSES = {
    "cancelled",
    "canceled"
}


def load_jsonl(path):
    """读取 JSONL 文件。"""
    records = []

    if not path.exists():
        return records

    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(
                    f"[警告] {path.name} 第 "
                    f"{line_number} 行解析失败：{exc}"
                )

    return records


def save_jsonl(path, records):
    """保存 JSONL 文件。"""
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(
                json.dumps(
                    record,
                    ensure_ascii=False
                ) + "\n"
            )


def normalize_status(value):
    """统一提取状态文本。"""
    if isinstance(value, dict):
        value = (
            value.get("desc")
            or value.get("status")
            or value.get("code")
        )

    if value is None:
        return ""

    return str(value).strip().lower()


def has_error(value):
    """判断异常字段是否包含实际内容。"""
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, dict):
        return bool(value)

    if isinstance(value, (list, tuple)):
        return bool(value)

    return bool(value)


def get_error_info(error):
    """提取错误码和错误信息，同时保留原始异常结构。"""
    error_code = ""
    error_message = ""

    if isinstance(error, dict):
        error_code = str(
            error.get("error_code")
            or error.get("code")
            or error.get("errorCode")
            or ""
        )

        error_message = str(
            error.get("error_message")
            or error.get("message")
            or error.get("errorMessage")
            or error.get("desc")
            or ""
        )

    elif isinstance(error, str):
        error_message = error

    return error_code, error_message


def classify_failure(status, error):
    """仅根据明确的运行状态和异常字段进行分类。"""

    if status in TIMEOUT_STATUSES:
        return "node_timeout", "error", True

    if status in CANCELLED_STATUSES:
        return "node_cancelled", "warning", True

    if status in FAILED_STATUSES:
        return "node_execution_failed", "error", True

    if has_error(error):
        return "node_error", "warning", False

    return None, None, False


def create_failure_record(run_id, node, archive_ref):
    """生成单条节点失败记录。"""

    status = normalize_status(node.get("status"))
    error = node.get("error")

    failure_type, severity, confirmed_failure = (
        classify_failure(status, error)
    )
    tool_result = node.get("tool_result") or get_tool_result(node)
    tool_status = normalize_status(tool_result.get("status")) if tool_result else ""
    if tool_result and not confirmed_failure and (
        tool_status in FAILED_STATUSES | TIMEOUT_STATUSES | CANCELLED_STATUSES
        or tool_result.get("transport_succeeded") is False
    ):
        failure_type, severity = "tool_reported_error", "error"
        error = tool_result

    if failure_type is None:
        return None, False, False

    error_code, error_message = get_error_info(error)

    failure_id = (
        "failure-"
        + uuid.uuid4().hex[:12]
    )

    record = {
        "failure_id": failure_id,
        "run_id": run_id,
        "node_id": node.get("node_id") or "",
        "node_name": node.get("node_name") or "",
        "node_type": node.get("node_type") or "",
        "failure_type": failure_type,
        "severity": severity,
        "status": status,
        "error_code": error_code,
        "error_message": error_message,
        "error_detail": error,
        "start_time": node.get("start_time"),
        "end_time": node.get("end_time"),
        "duration_ms": node.get("duration_ms"),
        "evidence_ref": (
            f"{archive_ref}/nodes.jsonl"
        ),
        "detected_at": datetime.now().isoformat()
    }
    if tool_result:
        record["tool_status"] = tool_status
        record["tool_call_id"] = tool_result.get("call_id")

    return record, confirmed_failure, True


def diagnose_archive(archive_path):
    """分析单次 Run 归档。"""

    run_id = archive_path.name
    archive_ref = archive_reference(archive_path)

    runs = load_jsonl(
        archive_path / "runs.jsonl"
    )

    nodes = load_jsonl(
        archive_path / "nodes.jsonl"
    )

    if not runs:
        print(
            f"[警告] {run_id} 缺少 runs.jsonl"
        )
        return None, []

    run = runs[0]
    capture_error = run.get("capture_error")

    workflow_status = normalize_status(
        run.get("status")
    )

    failures = []
    failed_node_count = 0
    error_node_count = 0
    tool_error_count = 0

    for node in nodes:

        record, confirmed, has_issue = (
            create_failure_record(
                run_id,
                node,
                archive_ref
            )
        )

        if record is not None:
            failures.append(record)
            if record["failure_type"] == "tool_reported_error":
                tool_error_count += 1

        if confirmed:
            failed_node_count += 1

        if has_issue:
            error_node_count += 1

    # 工作流失败，但没有定位到具体失败节点
    # 只生成一条工作流级记录，避免重复统计
    if (
        (workflow_status in FAILED_STATUSES or capture_error)
        and failed_node_count == 0
    ):
        failures.append({
            "failure_id": (
                "failure-" + uuid.uuid4().hex[:12]
            ),
            "run_id": run_id,
            "node_id": "",
            "node_name": "工作流级异常",
            "node_type": "Workflow",
            "failure_type": capture_error["kind"] if capture_error else "unknown_failure",
            "severity": "error",
            "status": workflow_status,
            "error_code": str(capture_error.get("http_status", "")) if capture_error else "",
            "error_message": (
                "Runtime 调用或事件采集异常。"
                if capture_error
                else "工作流最终状态为失败，但未从节点记录中定位到明确失败节点。"
            ),
            "error_detail": capture_error,
            "start_time": run.get("start_time"),
            "end_time": run.get("end_time"),
            "duration_ms": run.get("duration_ms"),
            "evidence_ref": (
                f"{archive_ref}/runs.jsonl"
            ),
            "detected_at": datetime.now().isoformat()
        })

    # 运行诊断状态
    if failed_node_count > 0 or workflow_status in FAILED_STATUSES or capture_error:
        diagnostic_status = "failed"

    elif error_node_count > 0:
        diagnostic_status = "needs_review"

    elif workflow_status in {"succeeded", "success"}:
        diagnostic_status = "normal"

    else:
        diagnostic_status = "unknown"

    diagnostic = {
        "run_id": run_id,
        "workflow_status": workflow_status,
        "node_count": len(nodes),
        "failed_node_count": failed_node_count,
        "error_node_count": error_node_count,
        "tool_error_count": tool_error_count,
        "diagnostic_status": diagnostic_status,
        "failure_ids": [
            item["failure_id"]
            for item in failures
        ],
        "diagnosed_at": datetime.now().isoformat()
    }

    return diagnostic, failures


def main():
    parser = argparse.ArgumentParser(description="重建历史运行诊断汇总。")
    parser.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR)
    parser.add_argument("--output-dir", type=Path, default=ANALYSIS_DIR)
    args = parser.parse_args()
    if not args.archive_dir.is_dir():
        parser.error(f"找不到归档目录：{args.archive_dir}")

    diagnostics = []
    failures = []
    for run in iter_run_dirs(args.archive_dir):
        diagnostic, run_failures = diagnose_archive(run)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
            failures.extend(run_failures)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    save_jsonl(args.output_dir / DIAGNOSTIC_FILE.name, diagnostics)
    save_jsonl(args.output_dir / FAILURE_FILE.name, failures)
    print(f"已汇总 {len(diagnostics)} 次运行、{len(failures)} 条异常记录。")
    print(f"汇总目录：{args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
