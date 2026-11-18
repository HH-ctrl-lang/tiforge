
import json
import uuid
from datetime import datetime
from pathlib import Path


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


def create_failure_record(run_id, node, archive_name):
    """生成单条节点失败记录。"""

    status = normalize_status(node.get("status"))
    error = node.get("error")

    failure_type, severity, confirmed_failure = (
        classify_failure(status, error)
    )

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
            f"archive/{archive_name}/nodes.jsonl"
        ),
        "detected_at": datetime.now().isoformat()
    }

    return record, confirmed_failure, True


def diagnose_archive(archive_path):
    """分析单次 Run 归档。"""

    run_id = archive_path.name

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

    for node in nodes:

        record, confirmed, has_issue = (
            create_failure_record(
                run_id,
                node,
                archive_path.name
            )
        )

        if record is not None:
            failures.append(record)

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
                f"archive/{archive_path.name}/runs.jsonl"
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
        "diagnostic_status": diagnostic_status,
        "failure_ids": [
            item["failure_id"]
            for item in failures
        ],
        "diagnosed_at": datetime.now().isoformat()
    }

    return diagnostic, failures


def main():

    print(
        "========== TiForge 运行异常诊断 v0.5 =========="
    )

    if not ARCHIVE_DIR.exists():
        print("[失败] 找不到 archive 目录。")
        return

    ANALYSIS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    diagnostics = []
    all_failures = []

    archive_paths = sorted(
        path for path in ARCHIVE_DIR.iterdir()
        if path.is_dir()
    )

    print(
        f"\n发现历史 Run 数量：{len(archive_paths)}"
    )

    for archive_path in archive_paths:

        diagnostic, failures = diagnose_archive(
            archive_path
        )

        if diagnostic is None:
            continue

        diagnostics.append(diagnostic)
        all_failures.extend(failures)

        print(
            f"\nRun：{diagnostic['run_id']}"
        )
        print(
            f"状态：{diagnostic['diagnostic_status']}"
        )
        print(
            f"节点数：{diagnostic['node_count']}"
        )
        print(
            f"确认失败节点数："
            f"{diagnostic['failed_node_count']}"
        )
        print(
            f"异常节点数："
            f"{diagnostic['error_node_count']}"
        )

    save_jsonl(
        FAILURE_FILE,
        all_failures
    )

    save_jsonl(
        DIAGNOSTIC_FILE,
        diagnostics
    )

    print("\n========== 诊断汇总 ==========")

    print(
        f"分析 Run 数量：{len(diagnostics)}"
    )
    print(
        f"失败记录数量：{len(all_failures)}"
    )

    print(
        f"\n运行诊断文件：{DIAGNOSTIC_FILE.resolve()}"
    )
    print(
        f"失败记录文件：{FAILURE_FILE.resolve()}"
    )

    print("\n========== 诊断完成 ==========")


if __name__ == "__main__":
    main()
