
import json
import uuid
from datetime import datetime
from pathlib import Path


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


def get_user_fields(node):
    """提取 AgentArts 节点业务输出。"""

    outputs = node.get("outputs")

    if not isinstance(outputs, dict):
        return {}

    user_fields = outputs.get("userFields")

    if isinstance(user_fields, dict):
        return user_fields

    # 兼容没有 userFields 包装的结构
    return outputs


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
    archive_name,
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
            f"archive/{archive_name}/nodes.jsonl"
            f"#node_id={evidence_node_id}"
        ),

        "recorded_at": datetime.now().isoformat()
    }


def process_archive(archive_path):
    """提取一次运行中的科研结果。"""

    run_id = archive_path.name

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
                    archive_name=archive_path.name,
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
                    archive_name=archive_path.name,
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

    checked_candidates = checker_fields.get(
        "checked_candidates",
        []
    )

    if not isinstance(checked_candidates, list):
        checked_candidates = []

    for candidate in checked_candidates:

        candidate_id = candidate.get(
            "candidate_id",
            ""
        )

        passed = candidate.get("passed")

        if passed is True:
            outcome_type = "gate_passed"
            outcome_status = "passed"

        elif passed is False:
            outcome_type = "gate_rejected"
            outcome_status = "rejected"

        else:
            # 没有明确布尔结果，不擅自判断通过或拒绝
            continue

        outcomes.append(
            create_outcome(
                run_id=run_id,
                candidate_id=candidate_id,
                outcome_type=outcome_type,
                stage="constraint_checker",
                outcome_status=outcome_status,
                evidence_field=(
                    "outputs.userFields.checked_candidates"
                ),
                description=str(
                    candidate.get("issues", "")
                ),
                archive_name=archive_path.name,
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
                archive_name=archive_path.name,
                evidence_node_id=(
                    review_node.get("node_id")
                )
            )
        )

    return outcomes


def main():

    print(
        "========== "
        "TiForge 科研结果提取 v0.6 "
        "=========="
    )

    ANALYSIS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    archive_paths = sorted(
        [
            path for path in ARCHIVE_DIR.iterdir()
            if path.is_dir()
        ],
        key=lambda path: path.name
    )

    print(
        f"\n发现归档 Run 数量：{len(archive_paths)}"
    )

    all_outcomes = []

    for archive_path in archive_paths:

        outcomes = process_archive(
            archive_path
        )

        all_outcomes.extend(outcomes)

        print(
            f"\nRun：{archive_path.name}"
        )

        print(
            f"科研结果记录：{len(outcomes)}"
        )

    save_jsonl(
        OUTPUT_FILE,
        all_outcomes
    )

    print("\n========== 提取汇总 ==========")

    print(
        f"分析 Run 数量：{len(archive_paths)}"
    )

    print(
        f"科研结果记录数量：{len(all_outcomes)}"
    )

    print(
        f"\n输出文件：{OUTPUT_FILE.resolve()}"
    )

    print(
        "\n========== 科研结果提取完成 =========="
    )


if __name__ == "__main__":
    main()
