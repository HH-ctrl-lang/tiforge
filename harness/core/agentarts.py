"""Call the deployed proposer and validate advice against observed MCP results."""

import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

import requests


def sse_events(lines):
    data = []
    for line in lines:
        if not line:
            if data:
                yield json.loads("\n".join(data))
                data.clear()
        elif line.startswith("data:"):
            value = line[5:].removeprefix(" ")
            if value != "[DONE]":
                data.append(value)


def returned_evidence(value):
    """Find tool records in JSON results, including MCP text content wrappers."""
    if isinstance(value, dict):
        if isinstance(value.get("evidence_id"), str):
            yield value
        for child in value.values():
            yield from returned_evidence(child)
    elif isinstance(value, list):
        for child in value:
            yield from returned_evidence(child)
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return
        yield from returned_evidence(decoded)


def request_proposal(scope):
    url = os.environ.get("TIFORGE_PROPOSER_URL", "").strip()
    key = os.environ.get("TIFORGE_PROPOSER_API_KEY", "").strip()
    endpoint = urlsplit(url)
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username
            or endpoint.password or endpoint.query or endpoint.fragment or not key):
        raise ValueError("请配置有效的 TIFORGE_PROPOSER_URL（HTTPS）和 TIFORGE_PROPOSER_API_KEY。")
    request_id = uuid4().hex
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "text/event-stream",
        "X-Invoke-Mode": "debug",
        "x-hw-agentarts-session-id": uuid4().hex,
        "X-Request-Id": request_id,
    }
    query = "请调查此 TiForge 用例并按约定返回 JSON。\n" + json.dumps(scope, ensure_ascii=False)
    summary = None
    execution_id = None
    tool_evidence = {}
    finished = False
    try:
        with requests.post(url, headers=headers, json={"query": query}, stream=True,
                           timeout=(15, 300), allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError(f"AgentArts 返回 HTTP {response.status_code}（请求 {request_id}）。")
            response.encoding = "utf-8"
            for event in sse_events(response.iter_lines(decode_unicode=True)):
                if not isinstance(event, dict):
                    raise ValueError("AgentArts 返回了无效的 SSE 事件。")
                name = event.get("event")
                if name == "error":
                    raise ValueError(f"AgentArts 返回执行错误（请求 {request_id}）。")
                if name == "plugin_end":
                    for item in returned_evidence(event.get("content")):
                        tool_evidence[item["evidence_id"]] = item
                if name == "summary_response":
                    summary, execution_id = event.get("content"), event.get("executionId")
                if name == "end":
                    finished = True
                    break
    except requests.RequestException as exc:
        raise ValueError(f"AgentArts 调用中断（{type(exc).__name__}，请求 {request_id}）。") from None
    except json.JSONDecodeError:
        raise ValueError(f"AgentArts SSE 无法解析（请求 {request_id}）。") from None
    if not finished or not isinstance(summary, str):
        raise ValueError(f"AgentArts 未返回完整回答（请求 {request_id}）。")
    try:
        report = json.loads(summary)
    except json.JSONDecodeError:
        raise ValueError(f"智能体回答不是有效的 JSON（请求 {request_id}）。") from None
    return report, tool_evidence, {"request_id": request_id, "execution_id": execution_id}


def nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def text_list(value):
    return isinstance(value, list) and all(nonempty_text(item) for item in value)


def validate_report(report, tool_evidence, runs, scope):
    if not isinstance(report, dict):
        raise ValueError("智能体回答必须是 JSON 对象。")
    if any(report.get(key) != scope[key] for key in ("case_id", "workflow_version")):
        raise ValueError("智能体回答中的用例或工作流版本与请求不符。")
    allowed = {run["run_id"]: run for run in runs}
    run_ids = report.get("run_ids")
    if not text_list(run_ids) or not set(run_ids).issubset(allowed) or len(set(run_ids)) != len(run_ids):
        raise ValueError("智能体回答引用了无效的运行标识。")
    proposals = report.get("proposals")
    missing = report.get("missing_evidence")
    if not isinstance(proposals, list) or not text_list(missing):
        raise ValueError("智能体回答中的提案或证据缺失清单格式无效。")

    status = report.get("status")
    if status == "needs_review" and not proposals and missing:
        return
    if status != "proposed" or not proposals or not run_ids or scope.get("run_id", run_ids[0]) not in run_ids:
        raise ValueError("智能体回答中的状态、运行和提案不一致。")
    evidence = {
        item["evidence_id"]: item for run_id in run_ids if not allowed[run_id]["capture_incomplete"]
        for item in allowed[run_id]["evidence"]
    }
    checked = {key: item for key, item in evidence.items() if tool_evidence.get(key) == item}
    required_run = scope.get("run_id")
    primary_ids = {item["evidence_id"] for item in allowed[required_run]["evidence"]} if required_run else checked.keys()
    for proposal in proposals:
        if not isinstance(proposal, dict):
            raise ValueError("提案必须是 JSON 对象。")
        node, field, ids = (proposal.get(key) for key in ("target_node", "target_field", "evidence_ids"))
        acceptance = proposal.get("acceptance")
        if (not nonempty_text(node) or not nonempty_text(field) or not text_list(ids)
                or not ids or not set(ids).issubset(checked)
                or not nonempty_text(proposal.get("draft_change"))
                or not (nonempty_text(acceptance) or (text_list(acceptance) and acceptance))):
            raise ValueError("提案字段不完整，或引用了本次工具调用未返回的证据。")
        if not any(
            key in primary_ids and node in (checked[key]["node_name"], checked[key]["node_id"])
            and field == checked[key]["field"] and checked[key]["value"] != "missing"
            for key in ids
        ):
            raise ValueError("提案目标节点及字段无法在本次目标运行的引用证据中定位。")
        if field == "inputs.literature_query":
            raise ValueError("现有证据快照未包含字段绑定配置，需复核字段传递问题。")


def build_agent_report(store, case_id, workflow_version, run_id):
    scope = {"case_id": case_id, "workflow_version": workflow_version}
    if run_id:
        scope["run_id"] = run_id
    base = {**scope, "source": "agentarts", "run_ids": [], "status": "needs_review", "proposals": []}
    allowed_ids = [item["run_id"] for item in store.list_runs(case_id, workflow_version)["runs"]]
    if not allowed_ids or (run_id and run_id not in allowed_ids):
        return {**base, "missing_evidence": ["本地证据快照中没有匹配的已授权运行。"]}
    report, tool_evidence, trace = request_proposal(scope)
    try:
        validate_report(report, tool_evidence, [store.runs[key] for key in allowed_ids], scope)
    except ValueError as exc:
        return {**base, "agentarts": trace, "missing_evidence": [str(exc)]}
    keys = ("run_ids", "status", "proposals", "missing_evidence")
    return {**base, **{key: report[key] for key in keys}, "agentarts": trace}
