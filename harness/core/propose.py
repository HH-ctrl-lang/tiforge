"""Build offline Harness proposals from independently evaluated archives."""

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

from .common import load_run, node_fields, read_object, read_rows
from .evaluate import EVALUATION_VERSION, LITERATURE, evaluate_archive, load_cases


REQUIREMENTS = "研究需求解析"
QUERY_FIELD = "literature_query"
PROJECT = Path(__file__).resolve().parents[2]


def locate_query_failure(run):
    """Trace EMPTY_QUERY back through the two observable inputs."""
    archive = run / "nodes.jsonl"
    nodes = list(read_rows(archive))
    requirements = node_fields(nodes, REQUIREMENTS)
    request = node_fields(nodes, LITERATURE, source="inputs")
    response = node_fields(nodes, LITERATURE)
    parsed_query = requirements.get(QUERY_FIELD)
    tool_query = request.get(QUERY_FIELD)
    if (requirements.get("clarification_needed") is not False
            or response.get("status") != "error" or response.get("error_code") != "EMPTY_QUERY"
            or not isinstance(parsed_query, str) or not isinstance(tool_query, str) or tool_query.strip()):
        return None
    if parsed_query.strip():
        root = "query_binding"
        component, field = LITERATURE, f"inputs.{QUERY_FIELD}"
        draft = f"把文献 MCP 的输入 {QUERY_FIELD} 绑定到「{REQUIREMENTS}」的同名输出。"
    else:
        root = "empty_generated_query"
        component, field = REQUIREMENTS, f"outputs.{QUERY_FIELD}"
        draft = (
            "当 clarification_needed=false 且进入候选设计时，必须生成非空 literature_query。"
            "用户指定文献时保留记录编号和检索范围。未指定时，根据应用场景、设计目标和允许元素生成检索词。"
            "需要澄清时沿用现有澄清分支。"
        )
    return {
        "root": root,
        "component": component,
        "field": field,
        "draft": draft,
        "trace": {
            "archive": str(archive.resolve()),
            "checkpoints": [
                {"node": REQUIREMENTS, "source": "outputs", "field": QUERY_FIELD, "value": parsed_query},
                {"node": LITERATURE, "source": "inputs", "field": QUERY_FIELD, "value": tool_query},
                {"node": LITERATURE, "source": "outputs", "field": "error_code", "value": "EMPTY_QUERY"},
            ],
        },
    }


def build_proposals(cases, archive_roots):
    counts = Counter()
    unresolved = []
    groups = defaultdict(list)
    references = defaultdict(dict)
    seen = set()
    manifest_paths = set()
    for root in archive_roots:
        if not root.is_dir():
            raise ValueError(f"归档目录不存在：{root}")
        manifest_paths.update(path.resolve() for path in root.rglob("manifest.json"))

    for path in sorted(manifest_paths):
        run = path.parent
        try:
            manifest = read_object(path)
            case_id = manifest.get("case_id")
            if not isinstance(case_id, str) or case_id not in cases:
                counts["outside_suite"] += 1
                continue
            manifest = load_run(run, cases)
        except (OSError, ValueError) as exc:
            counts["invalid_archive"] += 1
            unresolved.append({"run_id": run.name, "archive": str(run), "reason": str(exc)})
            continue
        run_id = manifest["run_id"]
        if run_id in seen:
            counts["duplicate_run"] += 1
            continue
        seen.add(run_id)

        version = manifest["workflow_version"]
        expected = cases[case_id]["expected"]
        verdict = evaluate_archive(run, manifest, expected)
        status = verdict["status"]
        counts[status] += 1
        item = {"case_id": case_id, "run_id": run_id, "workflow_version": version, "archive": str(run)}
        if status == "pass":
            if expected["route"] == "design":
                literature = verdict["evidence"].get("literature", {})
                previous = references[version].get(case_id)
                if previous is None or run_id > previous["run_id"]:
                    references[version][case_id] = {**item, "literature_status": literature.get("status")}
            continue

        findings = verdict["findings"]
        location = None
        if status == "fail" and any(finding["code"] == "literature_lookup" for finding in findings):
            try:
                location = locate_query_failure(run)
            except (OSError, ValueError):
                pass
        if location is None:
            unresolved.append({**item, "status": status, "findings": findings})
            continue
        groups[(version, location["root"])].append({**item, **location})

    proposals = []
    for (version, root), items in sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        first = items[0]
        target_cases = sorted({item["case_id"] for item in items})
        passing_cases = references[version]
        same_cases = [passing_cases[case_id] for case_id in target_cases if case_id in passing_cases]
        # A different passing design case only illustrates the interface. It is not an oracle.
        examples = same_cases or [passing_cases[case_id] for case_id in sorted(passing_cases)]
        scope = "same_case_pass" if same_cases else "design_interface_only"
        identity = [version, root, sorted(item["run_id"] for item in items)]
        proposal_id = sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        proposals.append({
            "proposal_id": f"proposal-{proposal_id}",
            "status": "proposed",
            "baseline_workflow_version": version,
            "target_finding": "literature_lookup",
            "first_observed_deviation": {"node": first["component"], "field": first["field"], "reason": root},
            "draft_change": first["draft"],
            "evidence": [
                {key: item[key] for key in ("case_id", "run_id", "workflow_version", "trace")}
                for item in sorted(items, key=lambda item: item["run_id"])
            ],
            "references": [{**ref, "scope": scope} for ref in examples],
            "predicted_fixes": target_cases,
            "regression_cases": sorted(cases.keys() - set(target_cases)),
            "acceptance": [
                "进入设计且需检索时，解析输出非空查询，文献 MCP 收到同一查询。",
                "MCP 不再返回 EMPTY_QUERY。真实 no_evidence 可保留，并应传给下游。",
                "用冻结的同一用例集对照基线与候选，复查指定文献和澄清路径。",
            ],
            "regression_risk": "通用检索规则可能覆盖指定记录范围，或让应澄清的请求提前调用文献工具。",
        })

    return {
        "evaluator_version": EVALUATION_VERSION,
        "suite_sha256": sha256(json.dumps(cases, sort_keys=True).encode("utf-8")).hexdigest(),
        "counts": dict(sorted(counts.items())),
        "proposals": proposals,
        "unresolved": unresolved,
    }


def configure(parser):
    parser.add_argument("archives", nargs="*", type=Path,
                        help="归档根目录或批次目录，默认读取项目 logging/archive，可提供多个")
    parser.add_argument("--cases", type=Path, default=PROJECT / "harness" / "cases" / "v01.jsonl")
    parser.add_argument("--output", type=Path, help="把提案另存为新的 JSON 文件")
    agent = parser.add_mutually_exclusive_group()
    agent.add_argument("--agentarts", action="store_true", help="调用已部署的外环提案员")
    agent.add_argument("--model-agent", action="store_true", help="直连华为云 MaaS，在本机执行只读证据工具")
    parser.add_argument("--case", help="智能体模式下指定用例 ID")
    parser.add_argument("--run-id", help="智能体模式下指定本次调查目标")
    parser.add_argument("--workflow-version", help="默认从项目 config.json 读取")
    parser.add_argument("--evidence", type=Path, help="供智能体核验的脱敏证据快照")
    parser.set_defaults(run=run)


def run(args, parser):
    try:
        if args.agentarts or args.model_agent:
            from .agentarts import build_agent_report
            from .evidence import EvidenceStore, SNAPSHOT

            if not args.case:
                raise ValueError("智能体模式需要指定 --case。")
            if args.archives:
                raise ValueError("请用 --evidence 指定要核验的证据快照。")
            if args.case not in load_cases(args.cases):
                raise ValueError("指定用例不在当前用例集中。")
            version = args.workflow_version or read_object(PROJECT / "config.json").get("workflow_version")
            if not isinstance(version, str) or not version.strip():
                raise ValueError("config.json 中缺少 workflow_version。")
            source = "model_agent" if args.model_agent else "agentarts"
            report = build_agent_report(EvidenceStore(args.evidence or SNAPSHOT), args.case, version,
                                        args.run_id, source=source)
        else:
            if any((args.case, args.run_id, args.workflow_version, args.evidence)):
                raise ValueError("这些提案参数需要使用 --agentarts 或 --model-agent。")
            report = build_proposals(load_cases(args.cases), args.archives or [PROJECT / "logging" / "archive"])
        content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as destination:
                destination.write(content)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"无法生成提案：{exc}\n")
    print(content, end="")
    return 0
