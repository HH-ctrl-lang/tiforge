"""Record AgentArts workflow exports and compare executable node definitions."""

from difflib import unified_diff
from hashlib import sha256
import json
from pathlib import Path
import re

from .common import json_object, read_object


DIRECTORY = Path(__file__).resolve().parents[1] / "snapshots"
SCHEMA = "tiforge-workflow-snapshot-v1"
PROMPTS = ("system_prompt", "template_content", "format_instruction", "response_template", "struct_output_template")
SENSITIVE_KEYS = {
    "apikey", "authorization", "accesstoken", "refreshtoken", "token", "password",
    "secret", "secretkey", "accesskey", "clientsecret", "privatekey", "credential",
    "credentials", "providerauthdata", "providerauthmetadata", "serverconfig",
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def definition_hash(value):
    return sha256(encoded(value).encode("utf-8")).hexdigest()


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[redacted]" if re.sub(r"[^a-z]", "", key.lower()) in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def index_by(records, key, label):
    if not isinstance(records, list):
        raise ValueError(f"{label}必须是数组。")
    result = {}
    for record in records:
        value = record.get(key) if isinstance(record, dict) else None
        if not isinstance(value, str) or not value or value in result:
            raise ValueError(f"{label}存在缺失或重复的 {key}。")
        result[value] = record
    return result


def build_snapshot(source, version):
    return snapshot_export(source.read_bytes(), source.name, version)


def load_export(raw):
    records = [json_object(line, f"DSL 第 {number} 行") for number, line in
               enumerate(raw.decode("utf-8-sig").splitlines(), 1) if line.strip()]
    workflows = [row for row in records if row.get("resource_type") == "workflow"]
    if len(workflows) != 1:
        raise ValueError("请导出恰好一条工作流。")
    workflow = workflows[0]
    dsl = workflow.get("dsl")
    if workflow.get("schema_version") != "V2.0" or not isinstance(dsl, dict):
        raise ValueError("当前支持包含 dsl 对象的 AgentArts V2.0 工作流导出。")
    if not isinstance(dsl.get("id"), str) or not dsl["id"] or dsl["id"] != workflow.get("resource_id"):
        raise ValueError("工作流资源 ID 与 DSL ID 不一致。")
    return records, workflow


def snapshot_export(raw, filename, version):
    records, workflow = load_export(raw)
    dsl = workflow["dsl"]
    graph = index_by(dsl.get("nodes"), "id", "节点")
    edges = dsl.get("edges")
    if not graph or not isinstance(edges, list) or not all(isinstance(edge, dict) for edge in edges):
        raise ValueError("DSL 缺少有效的节点或连线。")
    nodes = {}
    for node_id, node in graph.items():
        body = redact(node)
        configs = body.get("configs", {})
        if not isinstance(configs, dict):
            raise ValueError(f"节点 {node_id} 的 configs 必须是对象。")
        body["prompts"] = {key: configs.pop(key) for key in PROMPTS if key in configs}
        body["inputs"] = index_by(body.get("inputs", []), "name", f"节点 {node_id} 的输入")
        nodes[node_id] = body
    references = index_by([row for row in records if row is not workflow], "resource_id", "关联资源")
    dependencies = [
        {key: references.get(resource_id, {}).get(key) for key in ("resource_type", "resource_name")}
        | {"resource_id": resource_id, "included_in_export": resource_id in references}
        for resource_id in sorted(set(workflow.get("level2_resources", [])) | references.keys())
    ]
    definition = {
        "nodes": nodes,
        "edges": sorted(redact(edges), key=encoded),
        "configs": redact(dsl.get("configs", {})),
        "dependencies": dependencies,
    }
    meta = workflow.get("metadata") or {}
    return {
        "schema": SCHEMA,
        "version": version,
        "workflow": {"id": dsl["id"], "name": workflow.get("resource_name")},
        "source": {
            "file": filename,
            "sha256": sha256(raw).hexdigest(),
            "dsl_schema": workflow["schema_version"],
            "platform_metadata": {key: meta[key] for key in
                                  ("status", "last_version_id", "deploy_wf_version", "published_at", "updated_at")
                                  if key in meta},
        },
        "definition_sha256": definition_hash(definition),
        "definition": definition,
    }


def lines(value):
    return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)).splitlines()


def diff_text(before, after, field):
    return list(unified_diff(lines(before), lines(after), f"baseline/{field}", f"candidate/{field}", lineterm=""))


def compare(before, after):
    for snapshot in (before, after):
        if (snapshot.get("schema") != SCHEMA or not isinstance(snapshot.get("definition"), dict)
                or snapshot.get("definition_sha256") != definition_hash(snapshot["definition"])):
            raise ValueError("快照格式无效或配置哈希不匹配。")
    if before["workflow"]["id"] != after["workflow"]["id"]:
        raise ValueError("两份快照必须来自同一个平台工作流。")
    baseline, candidate = before["definition"], after["definition"]
    old_nodes, new_nodes = baseline["nodes"], candidate["nodes"]
    changes = {}
    for node_id in sorted(old_nodes.keys() | new_nodes.keys()):
        old, new = old_nodes.get(node_id), new_nodes.get(node_id)
        if old == new:
            continue
        if old is None or new is None:
            changes[node_id] = {"change": "added" if old is None else "removed", "name": (new or old)["name"]}
            continue
        prompts = {
            field: diff_text(old["prompts"].get(field, ""), new["prompts"].get(field, ""), field)
            for field in old["prompts"].keys() | new["prompts"].keys()
            if old["prompts"].get(field) != new["prompts"].get(field)
        }
        inputs = {
            field: {"before": old["inputs"].get(field), "after": new["inputs"].get(field)}
            for field in old["inputs"].keys() | new["inputs"].keys()
            if old["inputs"].get(field) != new["inputs"].get(field)
        }
        old_other = {key: value for key, value in old.items() if key not in ("prompts", "inputs")}
        new_other = {key: value for key, value in new.items() if key not in ("prompts", "inputs")}
        changes[node_id] = {"change": "modified", "name": new["name"], "prompts": prompts, "inputs": inputs,
                            "other_diff": diff_text(old_other, new_other, "node")}
    return {
        "baseline": {key: before[key] for key in ("version", "definition_sha256", "source")},
        "candidate": {key: after[key] for key in ("version", "definition_sha256", "source")},
        "node_changes": changes,
        "workflow_changes": {
            field: diff_text(baseline[field], candidate[field], field)
            for field in ("edges", "configs", "dependencies") if baseline[field] != candidate[field]
        },
    }


def version_name(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("快照版本仅支持英文字母、数字、下划线和短横线，且须以字母或数字开头。")
    return value


def run(args, parser):
    try:
        if args.action == "save":
            version = version_name(args.version)
            snapshot = build_snapshot(args.dsl, version)
            args.directory.mkdir(parents=True, exist_ok=True)
            path = args.directory / f"{version}.json"
            content = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2)
            with path.open("x", encoding="utf-8") as file:
                file.write(content + "\n")
            nodes = snapshot["definition"]["nodes"]
            result = {"path": str(path.resolve()), "version": version, "nodes": len(nodes),
                      "source": snapshot["source"], "definition_sha256": snapshot["definition_sha256"]}
        else:
            before = read_object(args.directory / f"{version_name(args.baseline)}.json")
            after = read_object(args.directory / f"{version_name(args.candidate)}.json")
            result = compare(before, after)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"快照操作失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def configure(parser):
    actions = parser.add_subparsers(dest="action", required=True)
    save = actions.add_parser("save", help="从 AgentArts JSONL 创建版本快照")
    save.add_argument("version")
    save.add_argument("dsl", type=Path)
    diff = actions.add_parser("diff", help="按节点比较两份已保存的快照")
    diff.add_argument("baseline")
    diff.add_argument("candidate")
    for action in (save, diff):
        action.add_argument("--directory", type=Path, default=DIRECTORY, help="默认使用 harness/snapshots")
        action.set_defaults(run=run)
