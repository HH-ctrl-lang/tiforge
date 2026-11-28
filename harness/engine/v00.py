"""Compare two TiForge Runtime archive batches against one regression suite."""

import argparse
from hashlib import sha256
import json
from pathlib import Path


from ..core.common import load_batch
from ..core.evaluate import evaluate_run as _evaluate_run, load_cases


def evaluate_run(run, manifest, expected):
    return _evaluate_run(run, manifest, expected, engine="v00")


def compare(cases, baseline_dir, candidate_dir, target_case, change):
    if target_case not in cases or not change.strip():
        raise ValueError("需要提供用例集中的目标用例以及改动说明。")
    baseline, baseline_version = load_batch(baseline_dir, cases)
    candidate, candidate_version = load_batch(candidate_dir, cases)
    baseline_ids = {run.name for run, _ in baseline.values()}
    candidate_ids = {run.name for run, _ in candidate.values()}
    if baseline_version == candidate_version or baseline_ids & candidate_ids:
        raise ValueError("基线和候选需要不同的工作流版本和各自独立的 Run。")

    results = {}
    for case_id, case in cases.items():
        pair = {}
        for name, batch in (("baseline", baseline), ("candidate", candidate)):
            run, manifest = batch[case_id]
            status, issues = evaluate_run(run, manifest, case["expected"])
            pair[name] = {"run": str(run), "status": status, "issues": issues}
        results[case_id] = pair

    regressions = [
        case_id for case_id, pair in results.items()
        if pair["baseline"]["status"] == "pass" and pair["candidate"]["status"] == "fail"
    ]
    failed_candidates = [
        case_id for case_id, pair in results.items() if pair["candidate"]["status"] == "fail"
    ]
    target = results[target_case]
    if failed_candidates:
        decision, reason = "reject", "候选未通过固定用例。"
    elif any(result["status"] == "needs_review" for pair in results.values() for result in pair.values()):
        decision, reason = "needs_review", "至少一侧的运行证据不完整。"
    elif (target["baseline"]["status"], target["candidate"]["status"]) != ("fail", "pass"):
        decision, reason = "reject", "目标用例未从失败变为通过。"
    else:
        decision, reason = "accept", "目标用例改善，候选通过全部固定用例。"
    return {
        "scope": "structured_regression",
        "decision": decision,
        "reason": reason,
        "change": change,
        "target_case_id": target_case,
        "baseline_version": baseline_version,
        "candidate_version": candidate_version,
        "regressions": regressions,
        "cases": results,
    }


def configure(parser):
    parser.add_argument("--cases", type=Path, default=Path(__file__).resolve().parents[1] / "cases" / "v00.jsonl",
                        help="固定回归用例 JSONL")
    parser.add_argument("--baseline", type=Path, required=True, help="基线版本的批量归档目录")
    parser.add_argument("--candidate", type=Path, required=True, help="候选版本的批量归档目录")
    parser.add_argument("--target-case", "--target", dest="target", required=True, help="此次改动针对的用例 ID")
    parser.add_argument("--change", required=True, help="改动说明或对应的变更记录")
    parser.set_defaults(run=run)


def run(args, parser):
    try:
        cases = load_cases(args.cases)
        report = compare(cases, args.baseline, args.candidate, args.target, args.change)
        report["suite_sha256"] = sha256(json.dumps(cases, sort_keys=True).encode("utf-8")).hexdigest()
    except (OSError, ValueError) as exc:
        parser.exit(2, f"无法比较归档：{exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {"accept": 0, "reject": 1, "needs_review": 2}[report["decision"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    configure(parser)
    return run(parser.parse_args(), parser)


if __name__ == "__main__":
    raise SystemExit(main())
