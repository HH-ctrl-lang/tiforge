
import argparse
import json
import uuid
from datetime import datetime
from pathlib import Path

from .normalize_runtime_events import archive_reference, iter_run_dirs
from .tool_results import get_user_fields


BASE_DIR = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = BASE_DIR / "archive"
ANALYSIS_DIR = BASE_DIR / "analysis"

OUTPUT_FILE = ANALYSIS_DIR / "scientific_outcomes.jsonl"


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


def find_node(nodes, node_name):
    """根据节点名称查找节点。"""

    for node in nodes:

        if node.get("node_name") == node_name:
            return node

    return None


def create_outcome(
    run_id,
    candidate_id,
    outcome_type,
    stage,
    outcome_status,
    evidence_field,
    description,
    archive_ref,
    evidence_node_id
):
    """创建标准化科研结果记录。"""

    return {
        "outcome_id": (
            "outcome-" + uuid.uuid4().hex[:12]
        ),

        "run_id": run_id,

        "candidate_id": candidate_id or "",

        "outcome_type": outcome_type,

        "stage": stage,

        "outcome_status": outcome_status,

        "evidence_field": evidence_field,

        "description": description,

        "evidence_ref": (
            f"{archive_ref}/nodes.jsonl"
            f"#node_id={evidence_node_id}"
        ),

        "recorded_at": datetime.now().isoformat()
    }


def extract_literature(run_id, node, archive_ref):
    fields = get_user_fields(node)
    status = fields.get("status")
    if status not in ("success", "needs_review", "no_evidence"):
        return None
    outcome_type = {
        "success": "literature_evidence",
        "needs_review": "literature_pending",
        "no_evidence": "literature_no_evidence",
    }[status]
    output = node["outputs"]
    prefix = "outputs.userFields" if isinstance(output.get("userFields"), dict) else "outputs"
    outcome = create_outcome(
        run_id=run_id, candidate_id="", outcome_type=outcome_type, stage="literature",
        outcome_status=status, evidence_field=f"{prefix}.status",
        description=fields.get("query") or "", archive_ref=archive_ref, evidence_node_id=node.get("node_id"),
    )
    records = fields.get("records")
    outcome.update(
        call_id=fields.get("call_id"),
        dataset_version=fields.get("dataset_version"),
        record_ids=[row["record_id"] for row in records if isinstance(row, dict) and row.get("record_id")]
        if isinstance(records, list) else None,
        missing_evidence=fields.get("missing_evidence"),
    )
    return outcome


def process_archive(archive_path):
    """提取一次运行中的科研结果。"""

    run_id = archive_path.name
    archive_ref = archive_reference(archive_path)

    nodes = load_jsonl(
        archive_path / "nodes.jsonl"
    )

    if not nodes:
        print(f"[跳过] {run_id} 没有节点记录。")
        return []

    design_node = find_node(
        nodes,
        "候选材料设计"
    )

    checker_node = find_node(
        nodes,
        "材料成分约束检查"
    )

    review_node = find_node(
        nodes,
        "候选方案审查"
    )

    outcomes = []
    literature = find_node(nodes, "文献知识提取")
    if literature:
        outcome = extract_literature(run_id, literature, archive_ref)
        if outcome:
            outcomes.append(outcome)

    # =====================================================
    # 一、候选材料设计结果
    # =====================================================

    design_fields = get_user_fields(
        design_node
    ) if design_node else {}

    candidate_list = design_fields.get(
        "candidate_list",
        []
    )

    missing_information = design_fields.get(
        "missing_information",
        []
    )

    if not isinstance(candidate_list, list):
        candidate_list = []

    if not isinstance(missing_information, list):
        missing_information = []

    if candidate_list:

        for candidate in candidate_list:

            candidate_id = candidate.get(
                "candidate_id",
                ""
            )

            outcomes.append(
                create_outcome(
                    run_id=run_id,
                    candidate_id=candidate_id,
                    outcome_type="candidate_generated",
                    stage="candidate_design",
                    outcome_status="generated",
                    evidence_field=(
                        "outputs.userFields.candidate_list"
                    ),
                    description=(
                        "候选材料方案已生成。"
                        "该记录不代表材料性能已经验证。"
                    ),
                    archive_ref=archive_ref,
                    evidence_node_id=(
                        design_node.get("node_id")
                    )
                )
            )

    else:

        # 只有存在明确阻断证据时才记录设计阻断
        if missing_information:

            outcomes.append(
                create_outcome(
                    run_id=run_id,
                    candidate_id="",
                    outcome_type="design_blocked",
                    stage="candidate_design",
                    outcome_status="blocked",
                    evidence_field=(
                        "outputs.userFields.missing_information"
                    ),
                    description=(
                        "候选列表为空，且上游记录了缺失信息；"
                        "需要结合缺失信息确认是否属于主动阻断。"
                    ),
                    archive_ref=archive_ref,
                    evidence_node_id=(
                        design_node.get("node_id")
                    )
                )
            )

    # =====================================================
    # 二、成分约束检查结果
    # =====================================================

    checker_fields = get_user_fields(
        checker_node
    ) if checker_node else {}

    # 只有旧归档缺少三态判定字段时，才沿用布尔值。
    has_decisions = "candidate_decisions" in checker_fields
    result_field = "candidate_decisions" if has_decisions else "checked_candidates"
    gate_results = checker_fields.get(result_field, [])
    if not isinstance(gate_results, list):
        gate_results = []

    outputs = checker_node.get("outputs") if checker_node else None
    evidence_prefix = "outputs"
    if isinstance(outputs, dict) and isinstance(outputs.get("userFields"), dict):
        evidence_prefix += ".userFields"

    gate_statuses = {
        "pass": ("gate_passed", "passed"),
        "reject": ("gate_rejected", "rejected"),
        "needs_review": ("gate_pending", "needs_review"),
    }
    for candidate in gate_results:
        if not isinstance(candidate, dict):
            continue

        if has_decisions:
            status = candidate.get("status")
            reason = candidate.get("reason", "")
        else:
            passed = candidate.get("passed")
            if not isinstance(passed, bool):
                continue
            status = "pass" if passed else "reject"
            reason = candidate.get("issues", "")

        if not isinstance(status, str) or status not in gate_statuses:
            continue
        outcome_type, outcome_status = gate_statuses[status]

        outcomes.append(
            create_outcome(
                run_id=run_id,
                candidate_id=candidate.get("candidate_id", ""),
                outcome_type=outcome_type,
                stage="constraint_checker",
                outcome_status=outcome_status,
                evidence_field=f"{evidence_prefix}.{result_field}",
                description=str(reason),
                archive_ref=archive_ref,
                evidence_node_id=(
                    checker_node.get("node_id")
                )
            )
        )

    # =====================================================
    # 三、候选方案审查结果
    # =====================================================

    review_fields = get_user_fields(
        review_node
    ) if review_node else {}

    candidate_reviews = review_fields.get(
        "candidate_reviews",
        []
    )

    if not isinstance(candidate_reviews, list):
        candidate_reviews = []

    for candidate in candidate_reviews:
        if not isinstance(candidate, dict):
            continue

        candidate_id = candidate.get(
            "candidate_id",
            ""
        )

        alignment = candidate.get(
            "requirement_alignment"
        )

        if alignment == "consistent":

            outcome_type = "review_passed"
            outcome_status = "consistent"

        elif alignment == "conflict":

            outcome_type = "review_rejected"
            outcome_status = "conflict"

        elif alignment == "needs_review":

            outcome_type = "review_pending"
            outcome_status = "needs_review"

        else:
            continue

        outcomes.append(
            create_outcome(
                run_id=run_id,
                candidate_id=candidate_id,
                outcome_type=outcome_type,
                stage="review",
                outcome_status=outcome_status,
                evidence_field=(
                    "outputs.userFields.candidate_reviews"
                ),
                description=str(
                    candidate.get("review_conclusion", "")
                ),
                archive_ref=archive_ref,
                evidence_node_id=(
                    review_node.get("node_id")
                )
            )
        )

    return outcomes


def main():
    parser = argparse.ArgumentParser(description="重建历史科研结果汇总。")
    parser.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR)
    parser.add_argument("--output-dir", type=Path, default=ANALYSIS_DIR)
    args = parser.parse_args()
    if not args.archive_dir.is_dir():
        parser.error(f"找不到归档目录：{args.archive_dir}")

    outcomes = []
    for run in iter_run_dirs(args.archive_dir):
        outcomes.extend(process_archive(run))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / OUTPUT_FILE.name
    save_jsonl(output, outcomes)
    print(f"已汇总 {len(outcomes)} 条科研结果到 {output.resolve()}。")


if __name__ == "__main__":
    main()
