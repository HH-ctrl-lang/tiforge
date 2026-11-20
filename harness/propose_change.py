"""Generate a Harness proposal from a TiForge Run archive."""

import argparse
import json
from pathlib import Path
import re


COST_RANKING = re.compile(
    r"(?:成本|价格)(?:显著|明显)?(?:最低|最高|高于|低于|更低|更高)"
)
MISSING_COST_EVIDENCE = re.compile(
    r"(?:缺少|缺乏|没有|无|未提供)[^。；;\n]{0,20}(?:价格|成本数据|成本系数|成本模型)"
)
CHANGE = {
    "component": "候选材料设计",
    "change_type": "prompt",
    "scope": {"task_type": "cost_constrained"},
    "suggested_change": (
        "缺少价格数据或适用的成本模型时，将候选间的成本比较直接表述为"
        "待验证假设，并注明比较依据和所需数据。"
    ),
    "expected_effect": "减少缺少价格或成本模型支撑的确定性成本排序。",
    "possible_side_effect": "成本比较可能变得过于保守，回归时应检查是否仍保留定性成本取舍。",
}


def find_node(nodes, name):
    return next((node for node in nodes if node.get("node_name") == name), None)


def get_user_fields(node):
    outputs = node.get("outputs")
    if not isinstance(outputs, dict):
        return {}
    user_fields = outputs.get("userFields")
    if isinstance(user_fields, dict):
        return user_fields
    return outputs


def matching_evidence(design, review):
    candidates = get_user_fields(design).get("candidate_list")
    reviews = get_user_fields(review).get("candidate_reviews")
    if not isinstance(candidates, list) or not isinstance(reviews, list):
        return []

    candidates_by_id = {
        candidate["candidate_id"]: candidate
        for candidate in candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("candidate_id"), str)
    }
    evidence = []
    for candidate_review in reviews:
        if not isinstance(candidate_review, dict):
            continue
        candidate_id = candidate_review.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in candidates_by_id:
            continue
        rationale = candidates_by_id[candidate_id].get("design_rationale")
        if not isinstance(rationale, str) or not COST_RANKING.search(rationale):
            continue
        flags = candidate_review.get("risk_flags")
        if not isinstance(flags, list):
            continue
        matched_flags = [
            flag for flag in flags
            if isinstance(flag, str) and COST_RANKING.search(flag)
            and MISSING_COST_EVIDENCE.search(flag)
        ]
        if matched_flags:
            evidence.append({
                "candidate_id": candidate_id,
                "design": {
                    "node_id": design["node_id"],
                    "field": "candidate_list[].design_rationale",
                    "value": rationale,
                },
                "review": {
                    "node_id": review["node_id"],
                    "field": "candidate_reviews[].risk_flags",
                    "value": matched_flags,
                },
            })
    return evidence


def build_proposal(run_dir):
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json 必须为 JSON 对象。")
    nodes = [
        json.loads(line)
        for line in (run_dir / "nodes.jsonl").read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if not all(isinstance(node, dict) for node in nodes):
        raise ValueError("nodes.jsonl 每行必须为 JSON 对象。")

    parser_node = find_node(nodes, "研究需求解析")
    if not parser_node or get_user_fields(parser_node).get("task_type") != "cost_constrained":
        return None

    design = find_node(nodes, "候选材料设计")
    review = find_node(nodes, "候选方案审查")
    if not design or not review:
        return None

    evidence = matching_evidence(design, review)
    if not evidence:
        return None

    return {
        "rule_id": "unsupported_cost_ranking",
        "status": "pending_review",
        "run_id": manifest.get("run_id") or run_dir.name,
        "case_id": manifest.get("case_id"),
        "workflow_version": manifest.get("workflow_version"),
        "harness_version": manifest.get("harness_version"),
        "evidence_file": "nodes.jsonl",
        "evidence": evidence,
        "proposed_change": CHANGE,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="单次运行归档目录")
    args = parser.parse_args()

    try:
        proposal = build_proposal(args.run_dir)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"无法读取运行归档：{exc}\n")
    if proposal:
        print(json.dumps(proposal, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
