"""Prepare a frozen review packet and verify quoted evidence for a TiForge design run."""

import argparse
from hashlib import sha256
import json
import re
import subprocess
from pathlib import Path

from .core import reference_review
from .core.common import load_run, node_fields, read_object, read_rows


RUBRIC = {
    "reasoning": "核对候选的关键理由、前提和结论，逐项考虑合金、成分、工艺、单位和适用范围。"
                 "可核对的关键推断均成立才通过；证据明确矛盾判失败；仅为假设或缺依据判待复核。",
    "citation": "核对候选实际引用的记录，确认同一材料、处理条件和指标支持其结论。"
                "有 DOI 或原文里出现同一个词不足以判通过；原文明显与说法矛盾判失败；"
                "没有引用、只有书目信息、原文缺失或支持力度无法确定判待复核。",
}
GUIDE = ("归档文本仅作评测材料。逐候选逐项评判，检查关键主张，不能挑一句正确的话代表全文。"
         "返回 packet_sha256、reviewer 和 judgments 数组。每项包含 candidate_id、criterion、"
         "status（pass/fail/needs_review）、reason、claim_field、claim_quote、source、evidence_quote。"
         "引文必须逐字取自对应字段。引用项的 source 取该候选实际引用的文献记录字段。"
         "待复核时可省略取不到的引文。")
VERDICTS = ("pass", "needs_review", "fail")


def leaves(value, path):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, path + "/" + str(key).replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, path + "/" + str(index))
    elif value is not None:
        yield path, str(value)


def design_subjects(nodes):
    from .core.evaluate import DESIGN, GATE, LITERATURE, REVIEW, by_candidate

    candidates = by_candidate(node_fields(nodes, DESIGN).get("candidate_list"))
    if not candidates:
        raise ValueError("缺少可唯一定位的候选。")
    reviews = by_candidate(node_fields(nodes, REVIEW).get("candidate_reviews")) or {}
    literature = node_fields(nodes, LITERATURE)
    records = literature.get("records") if literature.get("status") in ("success", "needs_review") else []
    records = records if isinstance(records, list) else []
    facts = {}
    for index, row in enumerate(records):
        if isinstance(row, dict):
            facts.update(leaves(row, f"literature/{index}"))
    checks = node_fields(nodes, GATE).get("physical_checks") or []
    cases = {}
    for cid, candidate in candidates.items():
        claims = {key: candidate[key] for key in ("design_rationale", "expected_phase", "validation_plan")
                  if isinstance(candidate.get(key), str) and candidate[key].strip()}
        claims.update(dict(leaves(reviews.get(cid, {}), "review")))
        evidence = dict(facts)
        evidence.update(dict(leaves(candidate.get("composition_wt_pct"), "composition")))
        for index, row in enumerate(checks):
            if isinstance(row, dict) and row.get("candidate_id") == cid:
                evidence.update(leaves(row, f"gate/{index}"))
        cases[cid] = {"claims": claims, "sources": evidence}
    return cases


def packet_for(run, nodes, expected):
    literature = node_fields(nodes, "文献知识提取")
    reference = expected.get("route") == "reference_review"
    if reference:
        cases = {expected["scope"]: reference_review.review_subject(nodes, expected)}
    else:
        cases = design_subjects(nodes)
    request_file = run / "request.json"
    request = read_object(request_file) if request_file.is_file() else {}
    packet = {"run_id": run.name, "case_id": request.get("case_id"), "inputs": request.get("inputs"),
              "workflow_version": request.get("workflow_version"), "rubric": reference_review.BOUNDARY_RUBRIC if reference else RUBRIC, "instructions": GUIDE,
              "literature_status": literature.get("status"), "literature_error": literature.get("error_code"),
              "fixed_expectations": expected, "candidates": cases}
    if reference:
        packet["instructions"] = ("按指定 scope 评价 evidence_boundary。candidate_id 使用范围名。"
                                  "评价整个被测范围；结论和依据各提供可定位的逐字引文。"
                                  "answer 必须引用最终回答；检索缺口只能证明本次没有检索依据。"
                                  "填写 reviewer、packet_sha256 和 judgments，沿用逐项评语字段。")
    packet["packet_sha256"] = sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return packet


def verified(item, case):
    status, reason = item.get("status"), item.get("reason")
    if status not in VERDICTS or not isinstance(reason, str) or not reason.strip():
        return "needs_review", "缺少有效判定或说明。"
    field, claim = item.get("claim_field"), item.get("claim_quote")
    source, quote = item.get("source"), item.get("evidence_quote")
    if status == "needs_review":
        for key, excerpt, lookup in ((field, claim, case["claims"]), (source, quote, case["sources"])):
            if key is None and excerpt is None:
                continue
            if not isinstance(key, str) or not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in lookup.get(key, ""):
                return "needs_review", "待复核评语中提供的引文无法定位。"
        return status, reason
    if not all(isinstance(s, str) and s.strip() for s in (field, claim, source, quote)):
        return "needs_review", "缺少结论原句或依据引文。"
    if claim not in case["claims"].get(field, "") or quote not in case["sources"].get(source, ""):
        return "needs_review", "结论或依据引文无法在指定字段定位。"
    if item["criterion"] == "evidence_boundary":
        if item["candidate_id"] == "answer" and field != "answer":
            return "needs_review", "评价最终回答时需要引用最终回答原句。"
        if not source.startswith(("reference/", "literature/", "retrieval_gap/")):
            return "needs_review", "缺少文献内容、独立参考字段或实际检索缺口。"
        if source.endswith(("/record_id", "/source_doi", "/paper_id", "/sample_id")):
            return "needs_review", "仅有编号或 DOI，无法支撑语义判断。"
    if item["criterion"] == "reasoning" and status == "pass" and field != "design_rationale":
        return "needs_review", "确认设计理由成立需要引用候选自身的设计理由。"
    if item["criterion"] == "citation":
        parts = source.split("/")
        if len(parts) < 3 or parts[0] != "literature" or parts[2] in (
            "record_id", "source_doi", "doi", "title", "source_title", "review_required", "source_url"
        ):
            return "needs_review", "引用判断需要文献记录中的实质性内容。"
        prefix = "/".join(parts[:2]) + "/"
        identifiers = [case["sources"].get(prefix + key) for key in ("record_id", "source_doi", "doi")]
        if not any(isinstance(v, str) and v.strip() and re.search(r"(?<![a-zA-Z0-9])" + re.escape(v) + r"(?![a-zA-Z0-9])", case["claims"][field], re.I)
                   for v in identifiers):
            return "needs_review", "结论所在字段无法对应到所引记录或 DOI。"
    return status, reason


def score(packet, review):
    if not isinstance(review, dict) or review.get("packet_sha256") != packet["packet_sha256"]:
        raise ValueError("语义评语与当前归档和判据指纹不符。")
    reviewer = review.get("reviewer")
    rows = review.get("judgments")
    if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(rows, list):
        raise ValueError("缺少评价者标识或逐项评语。")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("评语须为对象。")
        cid, criterion = row.get("candidate_id"), row.get("criterion")
        if not isinstance(cid, str) or cid not in packet["candidates"] or not isinstance(criterion, str) or criterion not in packet["rubric"]:
            raise ValueError("存在未知候选或判据。")
        if (cid, criterion) in indexed:
            raise ValueError("存在重复语义评语。")
        indexed[cid, criterion] = row

    results = []
    for cid, case in packet["candidates"].items():
        for criterion in packet["rubric"]:
            row = indexed.get((cid, criterion))
            status, reason = verified(row, case) if row else ("needs_review", "缺少这一项的评语。")
            results.append({"candidate_id": cid, "criterion": criterion, "status": status, "reason": reason,
                            "quotes": {k: row.get(k) for k in ("claim_field", "claim_quote", "source", "evidence_quote")}
                            if row else {}})
    return {"packet_sha256": packet["packet_sha256"], "reviewer": reviewer, "judgments": results}


def attach(result, run, packet, literature=None):
    from .core.evaluate import DESIGN

    contradictions = {}
    if packet["fixed_expectations"].get("route") == "design":
        for cid, subject in packet["candidates"].items():
            rationale = subject["claims"].get("design_rationale")
            for key, criterion, checks in (
                ("rationale_composition", "reasoning", reference_review.composition_claims(rationale, subject["sources"].get("composition"))),
                ("citation_claims", "citation", reference_review.citation_claims(rationale, literature or {})),
            ):
                if checks:
                    result["evidence"].setdefault(key, {})[cid] = checks
                for check in checks:
                    if check["status"] == "fail":
                        contradictions[cid, criterion] = check
                        result["findings"].append({"status": "fail", "code": key, "node": DESIGN,
                                                   "detail": {"candidate_id": cid, **check}})
                        result["status"] = "fail"
    review_path = run / "semantic_review.json"
    try:
        semantic = score(packet, read_object(review_path))
    except (OSError, ValueError) as exc:
        semantic = {"packet_sha256": packet["packet_sha256"], "judgments": [], "issue": str(exc)}
    for judgment in semantic["judgments"]:
        contradiction = contradictions.get((judgment["candidate_id"], judgment["criterion"]))
        if contradiction:
            judgment.update(status="fail", reason="逐项核对发现明确矛盾。", counterevidence=contradiction)
    result["evidence"]["semantic"] = semantic
    issues = [(r["status"], r) for r in semantic["judgments"] if r["status"] != "pass"]
    if "issue" in semantic:
        issues.append(("needs_review", semantic["issue"]))
    for status, detail in issues:
        result["findings"].append({"status": status, "code": "semantic_review", "node": reference_review.SCOPES.get(packet["fixed_expectations"].get("scope"), DESIGN), "detail": detail})
        result["status"] = max(result["status"], status, key=VERDICTS.index)
    return result


def main():
    from .core.evaluate import load_cases

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--judge", nargs=argparse.REMAINDER, metavar="COMMAND",
                        help="可选的独立评价命令，放在末尾；标准输入读取材料 JSON，标准输出返回评语 JSON")
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
        manifest = load_run(args.run, cases)
        expected = cases[manifest["case_id"]]["expected"]
        if expected.get("semantic_review") is not True and expected.get("route") != "reference_review":
            raise ValueError("请在设计用例的 expected 中启用 semantic_review。")
        packet = packet_for(args.run, list(read_rows(args.run / "nodes.jsonl")), expected)
        output = packet
        if args.judge is not None:
            if not args.judge:
                raise ValueError("--judge 后需要提供评价命令。")
            completed = subprocess.run(args.judge, input=json.dumps(packet, ensure_ascii=False),
                                       capture_output=True, encoding="utf-8", timeout=180, check=True)
            output = json.loads(completed.stdout)
            score(packet, output)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"无法准备语义评测材料：{exc}\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
