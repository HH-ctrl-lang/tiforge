import json
import uuid
from pathlib import Path
from datetime import datetime

from .tool_results import get_tool_result


BASE_DIR = Path(__file__).resolve().parents[1]
SCRATCH_DIR = BASE_DIR / "scratch"
INPUT_FILE = SCRATCH_DIR / "runtime_smoke_events.jsonl"

OUTPUT_DIR = SCRATCH_DIR / "normalized"
RUN_FILE = OUTPUT_DIR / "runs.jsonl"
NODE_FILE = OUTPUT_DIR / "nodes.jsonl"
EVENT_FILE = OUTPUT_DIR / "events.jsonl"


def load_events(path=INPUT_FILE):
    events = []

    with path.open("r", encoding="utf-8") as f:
        for index, line in enumerate(f, 1):
            line = line.strip()

            if not line:
                continue

            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    event["_source_index"] = index
                    events.append(event)

            except json.JSONDecodeError as e:
                print(f"第 {index} 行 JSON 解析失败：{e}")

    return events


def iter_run_dirs(root):
    """Find runs in both the archive root and nested batch directories."""
    if (root / "runs.jsonl").is_file():
        yield root
        return
    for run_file in sorted(root.rglob("runs.jsonl")):
        yield run_file.parent


def archive_reference(run_dir):
    """Use a logging-relative reference for local archives and an absolute one otherwise."""
    path = run_dir.resolve()
    if path.is_relative_to(BASE_DIR):
        path = path.relative_to(BASE_DIR)
    return path.as_posix()


def get_data(event):
    data = event.get("data")

    if isinstance(data, dict):
        return data

    return {}


def get_status_text(status):
    if isinstance(status, dict):
        return (
            status.get("desc")
            or status.get("status")
            or str(status)
        )

    if status is None:
        return None

    return str(status)


def get_node_key(data):
    """
    节点唯一身份优先使用 node_id。
    workflow_node_message 中没有 node_id 时，
    使用 componentId 与节点对应。
    """

    node_id = (
        data.get("node_id")
        or data.get("componentId")
    )

    if node_id:
        return node_id

    return None


def create_node_record(run_id, data):
    return {
        "run_id": run_id,

        "node_id": (
            data.get("node_id")
            or data.get("componentId")
        ),

        "node_name": (
            data.get("node_name")
            or data.get("componentName")
        ),

        "node_type": (
            data.get("node_type")
            or data.get("componentType")
        ),

        "execution_id": (
            data.get("execution_id")
            or data.get("executionId")
        ),

        "start_time": None,
        "end_time": None,
        "duration_ms": None,

        "status": None,

        "inputs": None,
        "outputs": None,
        "error": None,

        "trace_id": data.get("traceId"),
        "invoke_id": data.get("invokeId"),
        "parent_invoke_id": data.get(
            "parentInvokeId"
        ),

        "event_count": 0
    }


def build_nodes(events, run_id):

    nodes = {}

    # =====================================================
    # 第一阶段：根据节点生命周期事件建立节点
    # =====================================================

    for event in events:

        event_type = event.get("event")

        if event_type not in {
            "node_started",
            "node_finished",
            "node_wait"
        }:
            continue

        data = get_data(event)

        node_id = (
            data.get("node_id")
            or data.get("componentId")
        )

        if not node_id:
            continue

        if node_id not in nodes:

            nodes[node_id] = create_node_record(
                run_id,
                data
            )

        record = nodes[node_id]

        record["event_count"] += 1

        for field in ("inputs", "outputs", "error"):
            if data.get(field) or (record[field] is None and data.get(field) is not None):
                record[field] = data[field]

        # -------------------------------------------------
        # 基本信息
        # -------------------------------------------------

        if data.get("node_id"):
            record["node_id"] = data.get(
                "node_id"
            )

        if data.get("node_name"):
            record["node_name"] = data.get(
                "node_name"
            )

        if data.get("node_type"):
            record["node_type"] = data.get(
                "node_type"
            )

        if data.get("execution_id"):
            record["execution_id"] = data.get(
                "execution_id"
            )

        if data.get("executionId"):
            record["execution_id"] = data.get(
                "executionId"
            )

        # -------------------------------------------------
        # node_started
        # -------------------------------------------------

        if event_type == "node_started":

            start_time = (
                data.get("start_time")
                or data.get("startTime")
            )

            if start_time is not None:
                record["start_time"] = start_time

            status = get_status_text(
                data.get("status")
            )

            if status:
                record["status"] = status

        # -------------------------------------------------
        # node_finished
        # -------------------------------------------------

        elif event_type == "node_finished":

            end_time = (
                data.get("end_time")
                or data.get("endTime")
            )

            if end_time is not None:
                record["end_time"] = end_time

            # 最终状态必须以 node_finished 为准
            status = get_status_text(
                data.get("status")
            )

            if status:
                record["status"] = status

        # -------------------------------------------------
        # node_wait
        # -------------------------------------------------

        elif event_type == "node_wait":

            # 等待状态不能覆盖已经得到的最终状态
            if record["status"] is None:
                record["status"] = "waiting"

    # =====================================================
    # 第二阶段：
    # workflow_node_message 补充输入、输出、错误和调用链
    # =====================================================

    for event in events:

        if event.get("event") != "workflow_node_message":
            continue

        data = get_data(event)

        node_id = (
            data.get("node_id")
            or data.get("componentId")
        )

        if not node_id:
            continue

        if node_id not in nodes:

            nodes[node_id] = create_node_record(
                run_id,
                data
            )

        record = nodes[node_id]

        record["event_count"] += 1

        # -------------------------------------------------
        # 基本信息
        # -------------------------------------------------

        if data.get("componentName"):
            record["node_name"] = data.get(
                "componentName"
            )

        if data.get("componentType"):
            record["node_type"] = data.get(
                "componentType"
            )

        if data.get("executionId"):
            record["execution_id"] = data.get(
                "executionId"
            )

        # -------------------------------------------------
        # 输入输出
        # -------------------------------------------------

        if data.get("inputs") or (record["inputs"] is None and data.get("inputs") is not None):
            record["inputs"] = data.get(
                "inputs"
            )

        if data.get("outputs") or (record["outputs"] is None and data.get("outputs") is not None):
            record["outputs"] = data.get(
                "outputs"
            )

        # -------------------------------------------------
        # 错误
        # -------------------------------------------------

        if data.get("error") or (record["error"] is None and data.get("error") is not None):
            record["error"] = data.get(
                "error"
            )

        # -------------------------------------------------
        # 调用链
        # -------------------------------------------------

        if data.get("traceId"):
            record["trace_id"] = data.get(
                "traceId"
            )

        if data.get("invokeId"):
            record["invoke_id"] = data.get(
                "invokeId"
            )

        if data.get("parentInvokeId"):
            record["parent_invoke_id"] = data.get(
                "parentInvokeId"
            )

    # =====================================================
    # 第三阶段：计算节点耗时
    # =====================================================

    for record in nodes.values():
        tool_result = get_tool_result(record)
        if tool_result:
            record["tool_result"] = tool_result

        start = record["start_time"]
        end = record["end_time"]

        if (
            isinstance(start, (int, float))
            and isinstance(end, (int, float))
        ):
            record["duration_ms"] = (
                end - start
            )

    return list(nodes.values())


def build_run(events, nodes, run_id):

    start_time = None
    end_time = None
    status = "unknown"

    for event in events:

        event_type = event.get("event")
        data = get_data(event)

        if event_type == "workflow_started":

            start_time = data.get(
                "start_time"
            )

        elif event_type == "workflow_finished":

            end_time = data.get(
                "end_time"
            )

            status = get_status_text(
                data.get("status")
            ) or "unknown"

    duration_ms = None

    if (
        isinstance(start_time, (int, float))
        and isinstance(end_time, (int, float))
    ):
        duration_ms = end_time - start_time

    return {
        "run_id": run_id,
        "start_time": start_time,
        "end_time": end_time,
        "duration_ms": duration_ms,
        "status": status,
        "node_count": len(nodes),
        "event_count": len(events),
        "created_at": datetime.now().isoformat()
    }


def normalize_events(events, run_id):

    normalized = []

    for event in events:

        data = get_data(event)

        normalized.append({
            "run_id": run_id,
            "event_index": event.get(
                "_source_index"
            ),
            "event_type": event.get(
                "event"
            ),
            "created_time": event.get(
                "createdTime"
            ),

            "execution_id": (
                data.get("execution_id")
                or data.get("executionId")
            ),

            "node_id": (
                data.get("node_id")
                or data.get("componentId")
            ),

            "node_name": (
                data.get("node_name")
                or data.get("componentName")
            ),

            "node_type": (
                data.get("node_type")
                or data.get("componentType")
            ),

            "status": data.get("status"),

            "trace_id": data.get(
                "traceId"
            ),

            "invoke_id": data.get(
                "invokeId"
            ),

            "parent_invoke_id": data.get(
                "parentInvokeId"
            )
        })

    return normalized


def save_jsonl(path, records):

    with path.open(
        "w",
        encoding="utf-8"
    ) as f:

        for record in records:

            f.write(
                json.dumps(
                    record,
                    ensure_ascii=False
                ) + "\n"
            )


def main():

    print(
        "========== "
        "TiForge Runtime 日志标准化 v0.2 "
        "=========="
    )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    events = load_events()

    if not events:

        print("没有读取到有效事件。")
        return

    run_id = (
        "tf-"
        + datetime.now().strftime(
            "%Y%m%d-%H%M%S"
        )
        + "-"
        + uuid.uuid4().hex[:8]
    )

    print(f"Run ID：{run_id}")
    print(
        f"原始事件数量：{len(events)}"
    )

    nodes = build_nodes(
        events,
        run_id
    )

    normalized_events = normalize_events(
        events,
        run_id
    )

    run = build_run(
        events,
        nodes,
        run_id
    )

    save_jsonl(
        RUN_FILE,
        [run]
    )

    save_jsonl(
        NODE_FILE,
        nodes
    )

    save_jsonl(
        EVENT_FILE,
        normalized_events
    )

    print("\n========== 标准化结果 ==========")

    print("Run 数量：1")
    print(
        f"Node 数量：{len(nodes)}"
    )
    print(
        f"Event 数量：{len(normalized_events)}"
    )

    print("\n节点列表：")

    for node in nodes:

        print(
            f"- {node['node_name']} "
            f"| {node['node_type']} "
            f"| {node['status']} "
            f"| {node['duration_ms']} ms"
        )

    print("\n输出文件：")
    print(
        f"Run：{RUN_FILE.resolve()}"
    )
    print(
        f"Node：{NODE_FILE.resolve()}"
    )
    print(
        f"Event：{EVENT_FILE.resolve()}"
    )

    print(
        "\n========== 标准化完成 =========="
    )


if __name__ == "__main__":
    main()
