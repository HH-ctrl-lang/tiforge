"""Apply an evidence-backed prompt edit to an original AgentArts export."""

import json
from pathlib import Path
import re

from .agentarts import build_agent_report
from .common import read_object
from .evidence import EvidenceStore, SNAPSHOT
from .snapshot import DIRECTORY, compare, load_export, snapshot_export, version_name


TARGET_NAME = "研究需求解析"
TARGET_FIELD = "outputs.literature_query"
RULES = (
    "用户未提供可明确定位的检索目标，或明确要求不查文献时",
    "未提供明确的文献检索目标，literature_query 为空",
)


def locate_rules(prompt):
    lines = prompt.splitlines(keepends=True)
    selected = {}
    for rule in RULES:
        matches = [(str(i), line.rstrip("\r\n")) for i, line in enumerate(lines, 1) if rule in line]
        if len(matches) != 1:
            raise ValueError("基线提示词中无法唯一定位待改规则。")
        selected.update(matches)
    return lines, selected


def apply_edits(lines, selected, edits):
    if not isinstance(edits, dict) or edits.keys() != selected.keys():
        raise ValueError("提案必须为每条待改规则提供一行替换文本。")
    for number, original in selected.items():
        text = edits[number]
        if not isinstance(text, str):
            raise ValueError(f"提案中的第 {number} 行替换文本无效。")
        body = re.sub(r"^[0-9]+\s*[.．、]\s*", "", text.strip())
        prefix = original.partition(".")[0] + ". "
        new = prefix + body
        if (not body or len(new) > 600 or new == original or body.splitlines() != [body]
                or "literature_query" not in body):
            raise ValueError(f"提案中的第 {number} 行替换文本无效。")
        index = int(number) - 1
        lines[index] = new + lines[index][len(original):]
    return "".join(lines)


def require_evidence(store, run_id, node_id, proposal):
    run = store.runs[run_id]
    actual = {(item["node_id"], item["field"]): item["value"]
              for item in run["evidence"] if item["evidence_id"] in proposal["evidence_ids"]}
    lookup = [node["node_id"] for node in run["nodes"] if node["node_name"] == "文献知识提取"]
    if (len(lookup) != 1 or actual.get((node_id, TARGET_FIELD)) != "empty"
            or actual.get((node_id, "outputs.clarification_needed")) is not False
            or actual.get((lookup[0], "inputs.literature_query")) != "empty"
            or actual.get((lookup[0], "outputs.error_code")) != "EMPTY_QUERY"):
        raise ValueError("提案未引用目标运行中的完整空查询证据。")


def generate(args):
    old, new = version_name(args.baseline), version_name(args.candidate)
    if old.casefold() == new.casefold():
        raise ValueError("候选版本名必须与基线不同。")
    destination = args.directory / "candidates" / new
    if destination.exists():
        raise ValueError(f"候选目录已存在：{destination}")
    raw = args.dsl.read_bytes()
    baseline = read_object(args.directory / f"{old}.json")
    source = snapshot_export(raw, args.dsl.name, old)
    if baseline.get("source", {}).get("sha256") != source["source"]["sha256"]:
        raise ValueError("原始 DSL 与已保存的基线文件哈希不一致。")
    if baseline.get("definition") != source["definition"]:
        raise ValueError("原始 DSL 与基线配置快照不一致。")
    records, workflow = load_export(raw)
    targets = [node for node in workflow["dsl"]["nodes"] if node["name"] == TARGET_NAME and node["type"] == "LLM"]
    if len(targets) != 1:
        raise ValueError("无法唯一定位研究需求解析 LLM 节点。")
    target = targets[0]
    node_id = target["id"]
    prompt = target.get("configs", {}).get("system_prompt")
    if not isinstance(prompt, str):
        raise ValueError("目标节点缺少系统提示词。")
    lines, selected = locate_rules(prompt)
    store = EvidenceStore(args.evidence)
    report = build_agent_report(store, args.case, old, args.run_id, source="model_agent", prompt_rules=selected)
    if report["status"] != "proposed":
        raise ValueError("提案还需复核。 " + " ".join(report["missing_evidence"]))
    if len(report["proposals"]) != 1:
        raise ValueError("候选 DSL 当前要求一条针对研究需求解析的提案。")
    proposal = report["proposals"][0]
    if proposal["target_node"] not in (node_id, TARGET_NAME) or proposal["target_field"] != TARGET_FIELD:
        raise ValueError("提案超出允许编辑的节点和字段。")
    require_evidence(store, args.run_id, node_id, proposal)
    target["configs"]["system_prompt"] = apply_edits(lines, selected, proposal.get("prompt_edits"))
    filename = f"workflow-{new}.jsonl"
    payload = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in records).encode("utf-8")
    planned = snapshot_export(payload, filename, new)
    diff = compare(baseline, planned)
    change = diff["node_changes"].get(node_id, {})
    if (set(diff["node_changes"]) != {node_id} or diff["workflow_changes"]
            or set(change.get("prompts", {})) != {"system_prompt"}
            or change["inputs"] or change["other_diff"]):
        raise ValueError("候选 DSL 改动超出了系统提示词。")

    review = {"stage": "pending_import", "proposal": report, "diff": diff}
    destination.mkdir(parents=True, exist_ok=False)
    (destination / filename).write_bytes(payload)
    (destination / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "pending_import", "candidate": new, "dsl": str((destination / filename).resolve()),
            "review": str((destination / "review.json").resolve())}


def run(args, parser):
    try:
        result = generate(args)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"无法生成候选 DSL：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def configure(parser):
    parser.add_argument("baseline", help="已保存的基线快照版本")
    parser.add_argument("candidate", help="新的候选版本标签")
    parser.add_argument("dsl", type=Path, help="与基线快照对应的原始 AgentArts JSONL")
    parser.add_argument("--case", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--evidence", type=Path, default=SNAPSHOT)
    parser.add_argument("--directory", type=Path, default=DIRECTORY)
    parser.set_defaults(run=run)
