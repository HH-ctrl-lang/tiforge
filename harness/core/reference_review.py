"""Evaluate the scope of a literature lookup or final answer against a frozen case."""

from decimal import Decimal
import re

from .common import node_fields

SCOPES = {"literature": "文献知识提取", "answer": "结束"}
BOUNDARY_RUBRIC = {"evidence_boundary": (
    "对照固定判据，核对整段回答、审查说明（如有）和检索证据。能区分原始记录与目标条件、"
    "在证据不足时保留限制才通过；冒充实测、跨工艺套用或丢失关键警告判失败；无法判断则待复核。"
)}


def validate(expected, case_id):
    scope = expected.get("scope")
    if not isinstance(scope, str) or scope not in SCOPES or not isinstance(expected.get("criterion"), str) or not expected["criterion"].strip():
        raise ValueError(f"{case_id} 需要提供评价范围 scope 和固定文字判据 criterion。")
    for flag, allowed_scope in (("allow_empty_records", "literature"), ("require_review_summary", "answer")):
        if flag in expected and (type(expected[flag]) is not bool or scope != allowed_scope):
            raise ValueError(f"{case_id} 的 {flag} 仅适用于 {allowed_scope}，须为布尔值。")
    fields = expected.get("reference_fields")
    if fields is not None and (not isinstance(fields, dict) or not isinstance(fields.get("record_id"), str)
                               or not fields["record_id"].strip() or len(fields) < 2):
        raise ValueError(f"{case_id} 的参考字段需要记录 ID 和至少一个独立核对字段。")


def record_check(literature, fields, *, allow_empty=False):
    records = literature.get("records")
    if literature.get("status") not in ("success", "needs_review", "no_evidence") or not isinstance(records, list):
        return "needs_review", "文献调用缺少可核对的返回。"
    if not records and allow_empty:
        return "pass", "用例允许不返回原工艺对照记录，仍须复核有无正确说明目标工艺的证据缺口。"
    matches = [row for row in records if isinstance(row, dict) and row.get("record_id") == fields["record_id"]]
    if not matches:
        return "fail", f"应保留已知文献记录 {fields['record_id']}，实际返回的记录中没有这一条。"
    if len(matches) != 1:
        return "needs_review", "同一记录编号匹配多条结果，无法唯一核对。"
    row = matches[0]
    discrepancies = [key for key, value in fields.items() if key not in row or row[key] != value
                     or isinstance(row[key], bool) != isinstance(value, bool)]
    if discrepancies:
        return "fail", "文献记录与固定参考值不符或缺少字段：" + "、".join(discrepancies)
    return "pass", "记录编号和固定参考字段一致。"


def review_subject(nodes, expected):
    from ..semantic import leaves

    lit = node_fields(nodes, SCOPES["literature"])
    sources = dict(leaves(lit.get("records"), "literature"))
    sources.update(leaves(expected.get("reference_fields"), "reference"))
    if expected["scope"] == "answer":
        reply = node_fields(nodes, SCOPES["answer"], raw=True).get("responseContent")
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("缺少最终回答，无法判断其证据使用。")
        claims = {"answer": reply}
        review = node_fields(nodes, "候选方案审查")
        summary = review.get("review_summary")
        if expected.get("require_review_summary") and (not isinstance(summary, str) or not summary.strip()):
            raise ValueError("缺少审查说明，无法核对警告是否传到最终回答。")
        claims.update(leaves(summary, "review_summary"))
        sources.update(leaves(lit.get("missing_evidence"), "retrieval_gap"))
    else:
        claims = dict(leaves(lit.get("warnings"), "warnings"))
        claims.update(leaves(lit.get("missing_evidence"), "missing_evidence"))
        claims.update(leaves(lit.get("records"), "returned_record"))
    return {"claims": claims, "sources": sources}


def composition_claims(rationale, composition):
    """Check only explicit recipe quantities in affirmative clauses, in wt.%."""
    if not isinstance(rationale, str) or not isinstance(composition, str):
        return []
    amounts = {}
    for entry in composition.split(","):
        match = re.fullmatch(r"\s*([A-Z][a-z]?)\s*:\s*(\d+(?:\.\d+)?)\s*", entry)
        if not match or match[1] in amounts:
            return []
        amounts[match[1]] = Decimal(match[2])
    findings = []
    # Match entire clauses; skip projections, ranges, other alloys, quotes and comparisons.
    for clause in re.split(r"[，,。；;：:\n]", rationale):
        match = re.fullmatch(
            r"(?:本(?:方案|候选|配方)(?:把|将)?|将(?:昂贵|廉价)?|提高(?:昂贵|廉价)?)?\s*"
            r"([A-Z][a-z]?)\s*(?:略降至|降至|提高至|调至|含量为|至)\s*(\d+(?:\.\d+)?)\s*wt\.?\s*%", clause.strip())
        if match and match[1] in amounts:
            actual = amounts[match[1]]
            findings.append({"claim": clause.strip(), "element": match[1], "recipe_wt_pct": str(actual),
                             "status": "pass" if actual == Decimal(match[2]) else "fail"})
    return findings


def citation_claims(rationale, literature):
    """Check explicit single-record attributions in a bounded Chinese sentence form."""
    if not isinstance(rationale, str) or not isinstance(literature.get("records"), list):
        return []
    if literature.get("status") not in ("success", "needs_review"):
        return []
    properties = {"弹性模量": ("E_GPa", "GPa"), "屈服强度": ("YS_MPa", "MPa"),
                  "抗拉强度": ("UTS_MPa", "MPa")}
    findings = []
    for sentence in re.split(r"[。；;\n]", rationale):
        match = re.fullmatch(
            r"(?:据|根据|引用)\s*([A-Za-z][A-Za-z0-9_-]*)\s*(?:记录)?[，,]\s*"
            r"([\w＋+]{1,20}态)(?:试样)?的?(弹性模量|屈服强度|抗拉强度)(?:为|是|=)\s*"
            r"(\d+(?:\.\d+)?)\s*(GPa|MPa)", sentence.strip())
        if not match:
            continue
        rid, state, prop, number, unit = match.groups()
        field, record_unit = properties[prop]
        rows = [r for r in literature["records"] if isinstance(r, dict) and r.get("record_id") == rid]
        detail = {"claim": sentence.strip(), "record_id": rid, "metric": field, "status": "needs_review"}
        if len(rows) != 1:
            detail["reason"] = "本次检索未提供唯一对应记录。"
        else:
            row = rows[0]
            actual = row.get(field)
            detail["source"] = {k: row.get(k) for k in ("state", field, "source_doi", "source_loc", "review_required")}
            if not isinstance(row.get("state"), str) or not row["state"].strip() or type(actual) not in (float, int) or not row.get("source_loc") or not row.get("source_doi"):
                detail["reason"] = "记录缺少工艺、数值或可回查的文献位置。"
            elif row["state"].replace("＋", "+") != state.replace("＋", "+"):
                # Only distinct, known processing states can give a definite mismatch.
                known = {"铸态", "固溶态", "时效态", "固溶+时效态", "退火态", "轧制态"}
                different = {row["state"].replace("＋", "+"), state.replace("＋", "+")}
                detail["status"] = "fail" if different <= known else "needs_review"
                detail["reason"] = "所引记录的工艺状态与主张不一致，不能用它证明目标状态。"
            else:
                value = Decimal(number)
                if unit != record_unit:
                    value *= Decimal("1000") if unit == "GPa" else Decimal("0.001")
                equal = value == Decimal(str(actual))
                detail.update(status="pass" if equal else "fail",
                              reason="记录支持这句对原记录的转述。" if equal else "相同工艺下，主张数值与所引记录不符。")
        findings.append(detail)
    return findings
