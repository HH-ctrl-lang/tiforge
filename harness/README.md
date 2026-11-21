# TiForge Harness 基线

Harness 对同一批固定用例比较两个实际部署版本，输出逐用例结果和是否接受改动的建议。智果执行材料设计，`logging/` 负责调用和归档，`harness/` 负责评测与裁决。

## 准备回归

先确定目标用例和一项有明确预期的改动，保留基线部署，再发布独立的候选部署。比较期间固定用例、模型、物理守门规则和调用预算。保留改动差异以及实际部署标识。归档里的 `workflow_version` 是运行时填写的标签，需与实际调用的部署对应。

[cases.jsonl](cases.jsonl) 含医用、工业和 Nb 冲突三个用例。`inputs` 由日志脚本传给智果，`expected` 由 Harness 读取。准备本次评测时固定整份文件。目标用例应能复现待修问题，其余用例用于检查是否退化。

## 生成两批归档

在 TiForge 仓库根目录执行。按 [日志说明](../logging/README.md) 配置**基线部署**的地址和凭据，再运行第一条测试命令。将版本值改成实际工作流版本。随后切换到**候选部署**，运行第二条。每次比较使用新的输出目录。

```powershell
$experiment = Join-Path $env:TEMP ("tiforge-regression-" + [guid]::NewGuid().ToString("N"))
$baseline = Join-Path $experiment 'baseline'
$candidate = Join-Path $experiment 'candidate'

python .\logging\run_and_archive.py --cases .\harness\cases.jsonl --workflow-version '实际基线版本' --archive-dir $baseline

# 将日志调用配置切换到候选部署，再运行
python .\logging\run_and_archive.py --cases .\harness\cases.jsonl --workflow-version '实际候选版本' --archive-dir $candidate
```

批量命令可能因某个用例失败返回非零退出码。已生成的运行归档仍可供比较。每批目录必须对每个用例恰好有一次运行。比较前会核对用例 ID、实际输入和版本标签。

## 比较与查看裁决

```powershell
python -m harness.regression --cases .\harness\cases.jsonl --baseline $baseline --candidate $candidate --target 'nb-conflict-01' --change '修复 Nb 冲突时的澄清输出'
```

将 `--target` 和 `--change` 换成本次实际改动。报告输出为 JSON，包含两侧的版本、Run 路径、逐用例结果、退化用例及用例集指纹。

| 结果             | 含义                                                                                 | 退出码 |
| ---------------- | ------------------------------------------------------------------------------------ | ------ |
| `accept`       | 目标用例从失败变为通过，候选通过所有固定用例。                                       | 0      |
| `reject`       | 候选有用例失败，或目标用例没有改善。                                                 | 1      |
| `needs_review` | 至少一侧证据不足，且没有已确认的候选失败。归档或参数无法比较时也返回 2，并显示错误。 | 2      |

当前评测范围是候选数量、`composition_wt_pct` 的格式与元素和总量、成分守门结果，以及冲突时是否跳过候选生成并向用户返回澄清问题。成分格式沿用 `Ti:80,Nb:20`，用例要求总量为 100 wt.%。`accept` 只覆盖这组结构化检查。成本表述、文献证据和材料性能需要单独评测。团队核对报告后决定是否发布。

运行模块测试：

```powershell
python -m unittest harness.test_regression -v
```
