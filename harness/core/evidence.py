"""Export an explicit, read-only subset of Harness evidence for the proposer."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from .common import read_object, read_rows


PROJECT = Path(__file__).resolve().parents[2]
ARCHIVE = PROJECT / "logging" / "archive"
SNAPSHOT = ARCHIVE / "harness-evidence.json"
SCHEMA = "tiforge-evidence-v1"
PUBLIC_ERRORS = {"EMPTY_QUERY", "COMPOSITION", "DATA_COVERAGE"}
PUBLIC_STATES = {
    "success", "succeeded", "failed", "error", "unknown", "running", "waiting",
    "ok", "no_evidence", "pass", "reject", "flagged", "needs_review", "partial",
    "skipped", "design", "clarification",
}


def observed_fields(node, source):
    block = node.get(source)
    if not isinstance(block, dict):
        return {}
    return block.get("userFields", block)


def export_run(run, manifest):
    run_id = manifest["run_id"]
    evidence = []
    nodes = []

    def record(node_id, node_name, field, value):
        identity = [SCHEMA, run_id, node_id, node_name, field, value]
        digest = sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        evidence.append({
            "evidence_id": f"ev-{digest}", "node_id": node_id, "node_name": node_name,
            "field": field, "value": value,
        })

    capture = manifest.get("capture_error") or {}
    if capture:
        kind = capture.get("kind")
        record(None, None, "capture_error.kind", kind if kind in {
            "request_error", "request_timeout", "http_error", "incomplete_stream",
            "no_events", "interrupted",
        } else "other")
        exception = capture.get("exception_type")
        if exception in {"ConnectionError", "ChunkedEncodingError"}:
            record(None, None, "capture_error.exception_type", exception)

    for node in read_rows(run / "nodes.jsonl"):
        if node.get("run_id") != run_id:
            raise ValueError(f"{run_id} 的节点与运行标识不一致。")
        node_id, name = node.get("node_id"), node.get("node_name")
        if not isinstance(node_id, str) or not isinstance(name, str):
            continue
        nodes.append({"node_id": node_id, "node_name": name})
        status = node.get("status")
        if isinstance(status, str) and status.casefold() in PUBLIC_STATES:
            record(node_id, name, "status", status.casefold())
        for source in ("inputs", "outputs"):
            fields = observed_fields(node, source)
            if not isinstance(fields, dict):
                continue
            if "literature_query" in fields or name in {"研究需求解析", "文献知识提取"}:
                query = fields.get("literature_query")
                state = "missing"
                if "literature_query" in fields:
                    state = ("nonempty" if query.strip() else "empty") if isinstance(query, str) else "invalid"
                record(node_id, name, f"{source}.literature_query", state)
            for field in ("clarification_needed", "all_passed"):
                if isinstance(fields.get(field), bool):
                    record(node_id, name, f"{source}.{field}", fields[field])
            for field in ("status", "route"):
                value = fields.get(field)
                if isinstance(value, str) and value in PUBLIC_STATES:
                    record(node_id, name, f"{source}.{field}", value)
            code = fields.get("error_code")
            if isinstance(code, str) and code:
                record(node_id, name, f"{source}.error_code", code if code in PUBLIC_ERRORS else "OTHER")

    return {
        "run_id": run_id,
        "case_id": manifest["case_id"],
        "workflow_version": manifest["workflow_version"],
        "archived_at": manifest.get("archived_at"),
        "workflow_status": manifest.get("workflow_status"),
        "diagnostic_status": manifest.get("diagnostic_status"),
        "capture_incomplete": bool(capture),
        "configuration_snapshot_available": False,
        "nodes": nodes,
        "evidence": evidence,
    }


class EvidenceStore:
    def __init__(self, path):
        snapshot = read_object(path)
        if snapshot.get("schema") != SCHEMA or not isinstance(snapshot.get("runs"), list):
            raise ValueError("证据文件格式不受支持。")
        self.runs = {run["run_id"]: run for run in snapshot["runs"]}
        if not self.runs or len(self.runs) != len(snapshot["runs"]):
            raise ValueError("证据文件为空或存在重复运行。")

    def list_runs(self, case_id, workflow_version):
        keys = ("run_id", "case_id", "workflow_version", "archived_at", "workflow_status", "diagnostic_status")
        ordered = sorted(self.runs.values(), key=lambda run: run["run_id"], reverse=True)
        return {"runs": [
            {key: run[key] for key in keys}
            for run in ordered if run["case_id"] == case_id and run["workflow_version"] == workflow_version
        ]}

    def read_run_evidence(self, run_id, node=None):
        run = self.runs.get(run_id)
        if run is None:
            return {"error": "run_not_found"}
        if node is None:
            evidence = [item for item in run["evidence"] if item["node_id"] is None]
        else:
            matches = [item for item in run["nodes"] if node in (item["node_id"], item["node_name"])]
            if len(matches) != 1:
                return {"error": "node_not_found" if not matches else "ambiguous_node"}
            evidence = [item for item in run["evidence"] if item["node_id"] == matches[0]["node_id"]]
        return {**run, "evidence": evidence}


def main():
    from .common import load_run
    from .evaluate import load_cases

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", action="append", required=True, help="授权公开的 Run ID，可重复")
    parser.add_argument("--archives", type=Path, default=ARCHIVE)
    parser.add_argument("--cases", type=Path, default=PROJECT / "harness" / "cases" / "v01.jsonl")
    parser.add_argument("--output", type=Path, default=SNAPSHOT)
    args = parser.parse_args()
    try:
        root = args.archives.resolve()
        selected = set(args.run_id)
        runs = {}
        cases = load_cases(args.cases)
        for path in root.rglob("manifest.json"):
            if path.parent.name not in selected:
                continue
            run_path = path.parent.resolve()
            if not run_path.is_relative_to(root) or run_path.name in runs:
                raise ValueError(f"运行的归档路径不唯一或超出范围：{run_path.name}")
            manifest = load_run(run_path, cases)
            runs[run_path.name] = export_run(run_path, manifest)
        if missing := selected - runs.keys():
            raise ValueError(f"未找到运行：{', '.join(sorted(missing))}")
        content = json.dumps({"schema": SCHEMA, "runs": list(runs.values())}, ensure_ascii=False, indent=2)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(content + "\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"无法导出证据：{exc}\n")
    print(f"已导出 {len(runs)} 条运行至 {args.output}")


