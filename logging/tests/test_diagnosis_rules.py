
import json
import tempfile
from pathlib import Path

from scripts.diagnose_runs import (
    classify_failure,
    diagnose_archive,
    save_jsonl
)


def test_status_classification():
    """测试不同节点状态的分类结果。"""

    cases = [
        {
            "name": "正常节点",
            "status": "succeeded",
            "error": None,
            "expected": (None, None, False)
        },
        {
            "name": "普通执行失败",
            "status": "failed",
            "error": None,
            "expected": (
                "node_execution_failed",
                "error",
                True
            )
        },
        {
            "name": "执行超时",
            "status": "timeout",
            "error": None,
            "expected": (
                "node_timeout",
                "error",
                True
            )
        },
        {
            "name": "执行取消",
            "status": "cancelled",
            "error": None,
            "expected": (
                "node_cancelled",
                "warning",
                True
            )
        },
        {
            "name": "状态成功但存在异常信息",
            "status": "succeeded",
            "error": {
                "code": "TEST001",
                "message": "模拟异常"
            },
            "expected": (
                "node_error",
                "warning",
                False
            )
        }
    ]

    print("========== 状态分类测试 ==========")

    passed = 0

    for case in cases:

        actual = classify_failure(
            case["status"],
            case["error"]
        )

        expected = case["expected"]

        success = actual == expected

        print(
            f"[{'通过' if success else '失败'}] "
            f"{case['name']}"
        )

        if not success:
            print("预期：", expected)
            print("实际：", actual)
        else:
            passed += 1

    print(f"\n分类测试：{passed}/{len(cases)}")

    return passed == len(cases)


def test_archive_diagnosis():
    """使用临时模拟归档测试完整诊断流程。"""

    print("\n========== 完整诊断测试 ==========")

    with tempfile.TemporaryDirectory() as temp:

        root = Path(temp)

        cases = [
            {
                "name": "正常运行",
                "run_status": "succeeded",
                "node_status": "succeeded",
                "error": None,
                "expected": "normal",
                "expected_failures": 0
            },
            {
                "name": "节点执行失败",
                "run_status": "failed",
                "node_status": "failed",
                "error": {
                    "code": "TEST002",
                    "message": "模拟节点执行失败"
                },
                "expected": "failed",
                "expected_failures": 1
            },
            {
                "name": "存在待复核异常",
                "run_status": "succeeded",
                "node_status": "succeeded",
                "error": {
                    "code": "TEST003",
                    "message": "模拟异常信息"
                },
                "expected": "needs_review",
                "expected_failures": 1
            },
            {
                "name": "工作流失败但未定位节点",
                "run_status": "failed",
                "node_status": "succeeded",
                "error": None,
                "expected": "failed",
                "expected_failures": 1
            }
        ]

        passed = 0

        for index, case in enumerate(cases):

            archive = root / f"test_run_{index}"
            archive.mkdir()

            run = {
                "run_id": archive.name,
                "status": case["run_status"],
                "start_time": 1000,
                "end_time": 2000,
                "duration_ms": 1000
            }

            node = {
                "run_id": archive.name,
                "node_id": "node_test",
                "node_name": "测试节点",
                "node_type": "LLM",
                "status": case["node_status"],
                "error": case["error"],
                "start_time": 1000,
                "end_time": 2000,
                "duration_ms": 1000
            }

            save_jsonl(
                archive / "runs.jsonl",
                [run]
            )

            save_jsonl(
                archive / "nodes.jsonl",
                [node]
            )

            diagnostic, failures = diagnose_archive(
                archive
            )

            success = (
                diagnostic["diagnostic_status"]
                == case["expected"]
                and len(failures)
                == case["expected_failures"]
            )

            print(
                f"[{'通过' if success else '失败'}] "
                f"{case['name']}"
            )

            if not success:
                print("实际诊断：", diagnostic)
                print("实际失败记录：", failures)
            else:
                passed += 1

        print(f"\n完整诊断测试：{passed}/{len(cases)}")

        return passed == len(cases)


def main():

    result1 = test_status_classification()
    result2 = test_archive_diagnosis()

    print("\n========== 最终测试结果 ==========")

    if result1 and result2:
        print("全部模拟异常测试通过。")
    else:
        print("存在未通过的测试，需要检查诊断逻辑。")


if __name__ == "__main__":
    main()
