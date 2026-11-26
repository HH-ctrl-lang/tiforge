# TiForge Harness

Harness 读取 [logging 归档](../logging/README.md)，按用例检查已部署工作流，输出问题证据和版本对照结果。

## 目录结构

| 位置 | 内容 |
| --- | --- |
| `engine/common.py` | 读取用例和归档，校验批次、提取节点字段、检查候选成分格式 |
| `engine/v00.py`、`engine/v01.py` | 每个 Harness 版本各自的评测和对照逻辑 |
| `cases/v00.jsonl`、`cases/v01.jsonl` | 版本对应的默认用例集。每行包含 `case_id`、`inputs`、`expected` |
| `tests/` | 评测逻辑的单元测试，使用模拟归档 |
| `__main__.py` | 统一命令入口，子命令选择 Harness 版本 |

`regression.py`、`v01.py` 在目录顶层提供旧命令和导入路径的兼容入口。默认用例集中在 `cases/`。自定义用例可通过 `--cases` 指定。

## 版本与用途

| 版本 | 默认用例 | 检查范围 | 对照结果 |
| --- | --- | --- | --- |
| v00 | 3 条，医用、工业、Nb 冲突 | 路由、候选数量和配方、旧版布尔守门 | `accept`、`reject`、`needs_review` |
| v01 | 4 条，Ti001、Ti999、工业降本、Nb 冲突 | 路由、文献检索与传递、配方、物理守门三态、审查和科研结果归档 | `ready_for_full_regression`、`reject`、`needs_review` |

v08 使用 v01。原 v00 的设计用例需要 `all_passed=true`，新版物理守门待复核会被其判失败。v01 核对基础成分和逐候选守门事实，预期的待复核可以通过结构检查。v01 的修改建议按问题代码查预设规则，由团队据此准备实际改动。

## 运行和对照

以下命令从 TiForge 仓库根目录执行。先把日志调用配置指向实际部署。每次采集使用新的归档目录，`--workflow-version` 和 `--harness-version` 记录本批标签。

```powershell
$baseline = Join-Path $env:TEMP ('tiforge-v08-' + [guid]::NewGuid().ToString('N'))
python -B .\logging\run_and_archive.py --cases .\harness\cases\v01.jsonl --workflow-version v08 --harness-version v01 --archive-dir $baseline
python -B -m harness v01 --baseline $baseline
```

日志脚本遇到异常会继续执行其余用例，并可能返回非零退出码。已生成的归档仍可评测。单批 v01 报告中的 `pass`、`fail`、`needs_review` 是用例结果，整批退出码分别为 0（全通过）、1（有失败）、2（只有证据不足或参数、归档错误）。

有独立候选部署时，用相同用例重新采集到 `$candidate`。比较时固定模型、守门规则、工具及数据版本和预算，并记录实际部署标识。以下是当前工业检索问题的对照示例。

```powershell
python -B -m harness v01 --baseline $baseline --candidate $candidate --target-case industrial-cost --target-finding literature_lookup
```

目标问题在基线有证据，且候选批次全通过时，可继续跑团队提供的完整固定基准。两侧必须有各自独立的运行，使用同一份用例。

评测历史使用 v00 时，两批归档应使用 `cases/v00.jsonl` 采集。

```powershell
python -B -m harness v00 --baseline $baseline --candidate $candidate --target-case nb-conflict-01 --change '修复 Nb 冲突时的澄清输出'
python -B -m unittest discover -s harness/tests -v
```

## 独立评价一次内环运行

归档生成后，可以只评价一条 Run。该命令读取固定用例和归档，核对用例 ID、实际输入和版本，
再输出单条的 `pass`、`fail` 或 `needs_review`，退出码分别为 0、1、2。
设计和澄清用例默认使用组里的 v01 规则。复查旧用例时指定 `--engine v00`。

```text
python -m harness.evaluate --engine v00 --cases harness/cases/v00.jsonl --run logging/archive/tf-20261120-193643-db590628b0b1
```

第 6.4 节的失败分支可在固定用例的 `expected` 中用 `route: failure_handling` 和
`checks` 指定。触发条件由测试输入提供，期望处理方式由固定用例提供。
每项检查指明节点、结构化字段和期望值。例如，下面用一条总和错误的配方验证守门节点。
如果节点返回拒绝、对应规则编号和原因，这条用例通过。

```json
{
  "route": "failure_handling",
  "checks": [
    {
      "type": "hard_constraint_violation",
      "node": "材料成分约束检查",
      "field": "candidate_decisions",
      "where": {"candidate_id": "C01"},
      "equals": {"status": "reject", "rule_id": "COMPOSITION"},
      "required_text": ["reason"]
    }
  ]
}
```

| 失败类型 | 固定用例应核对的处理结果 |
| --- | --- |
| `hard_constraint_violation` | 拒绝对应候选，返回规则 ID 和原因 |
| `out_of_domain` | 标记超出数据范围，如 `physical_checks` 中对应 `DATA_COVERAGE` 的 `flagged` 记录 |
| `prediction_uncertainty` | 降低可信等级，给出补充验证建议 |
| `insufficient_evidence` | 提示缺什么证据，要求补充 |
| `tool_error` | 根据错误码重试、修正或停止，核对实际处理结果 |
| `insufficient_budget` | 停止高成本验证，返回当前验证等级 |

`field` 可省略，此时核对节点输出对象本身。列表输出通过 `where` 选择唯一一条记录。
默认读取节点输出，也可以用 `source: error` 读取节点错误对象。
证据明确和预期不符时判失败，缺少记录或字段时转待复核。
正常完成的分支默认要求 `succeeded`。需要验证工作流按规定停止时，可在该用例的
`expected.workflow_status` 指定 `failed`，仍需通过该用例定义的各项检查。
用例集须按具体节点实际提供的结构化字段来写。自定义失败分支用例可通过本单次评价命令运行。
