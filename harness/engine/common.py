"""Read and validate Harness cases, run archives, and compositions."""

from decimal import Decimal
import json


def json_object(text, source):
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f"{source} 必须是 JSON 对象。")
    return value


def read_object(path):
    return json_object(path.read_text(encoding="utf-8-sig"), path)


def read_rows(path):
    with path.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                yield json_object(line, f"{path}:{number}")


def validate_expected(expected, case_id):
    if expected.get("route") == "design":
        count = expected.get("candidate_count")
        elements = expected.get("allowed_elements")
        if type(count) is not int or count < 1 or not isinstance(elements, list) or not elements:
            raise ValueError(f"{case_id} 必须提供正整数 candidate_count 和非空 allowed_elements。")
        if not all(isinstance(element, str) and element.strip() for element in elements):
            raise ValueError(f"{case_id} 的 allowed_elements 必须为元素名称数组。")
    elif expected.get("route") != "clarification":
        raise ValueError(f"{case_id} 的 route 必须为 design 或 clarification。")


def load_cases(path, *, validate=validate_expected):
    cases = {}
    for case in read_rows(path):
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in cases:
            raise ValueError(f"无效或重复的 case_id：{case_id!r}。")
        inputs = case.get("inputs")
        expected = case.get("expected")
        if not isinstance(inputs, dict) or not inputs or not isinstance(expected, dict):
            raise ValueError(f"{case_id} 必须提供 inputs 和 expected 对象。")
        validate(expected, case_id)
        cases[case_id] = case
    if not cases:
        raise ValueError("回归用例集不能为空。")
    return cases


def load_run(run, cases):
    manifest = read_object(run / "manifest.json")
    request = read_object(run / "request.json")
    case_id = manifest.get("case_id")
    version = manifest.get("workflow_version")
    if not isinstance(case_id, str) or case_id not in cases:
        raise ValueError(f"{run.name} 的用例不在本次用例集中：{case_id!r}。")
    if manifest.get("run_id") != run.name or request.get("case_id") != case_id:
        raise ValueError(f"{run.name} 的请求与归档标识不一致。")
    if request.get("inputs") != cases[case_id]["inputs"]:
        raise ValueError(f"{run.name} 的实际输入与回归用例 {case_id} 不一致。")
    if not isinstance(version, str) or not version.strip() or request.get("workflow_version") != version:
        raise ValueError(f"{run.name} 的工作流版本缺失或不一致。")
    return manifest


def load_batch(directory, cases):
    batch = {}
    for run in sorted(directory.iterdir()):
        if not run.is_dir() or not run.name.startswith("tf-"):
            continue
        manifest = load_run(run, cases)
        case_id = manifest["case_id"]
        if case_id in batch:
            raise ValueError(f"{run.name} 有重复运行：{case_id!r}。")
        batch[case_id] = (run, manifest)
    missing = sorted(cases.keys() - batch.keys())
    if missing:
        raise ValueError(f"{directory} 缺少用例归档：{', '.join(missing)}。")
    versions = {manifest["workflow_version"] for _, manifest in batch.values()}
    if len(versions) != 1:
        raise ValueError(f"{directory} 必须只包含一个工作流版本。")
    return batch, versions.pop()


def node_fields(nodes, name, *, raw=False, source="outputs"):
    matches = [node for node in nodes if node.get("node_name") == name]
    if not matches:
        return {}
    if len(matches) != 1:
        raise ValueError(f"存在多条「{name}」节点记录。")
    outputs = matches[0].get(source)
    if isinstance(outputs, dict):
        fields = outputs if raw else outputs.get("userFields", outputs)
        if isinstance(fields, dict):
            return fields
    return {}


def check_composition(text, allowed):
    if not isinstance(text, str):
        return False
    amounts = {}
    try:
        for part in text.split(","):
            element, number = (value.strip() for value in part.split(":"))
            if element not in allowed or element in amounts:
                return False
            amount = Decimal(number)
            if not amount.is_finite() or not 0 < amount <= 100:
                return False
            amounts[element] = amount
        return sum(amounts.values()) == 100
    except (ValueError, ArithmeticError):
        return False
