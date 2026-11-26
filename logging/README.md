# TiForge Runtime 测试与日志

在 TiForge 仓库根目录打开 PowerShell，进入日志模块并安装依赖。后续日志命令均在 `logging/` 下执行。

```powershell
Set-Location -LiteralPath '.\logging'
python -m pip install requests
```

首次克隆、更新代码及提交改动见 [TiForge README](../README.md)。

## 目录结构

```text
logging/
├── README.md
├── run_and_archive.py  日常测试入口
├── config/             版本配置与本机凭据
├── cases/              批量用例与示例
├── scripts/            采集、标准化、归档与分析
├── tests/              诊断规则测试
├── archive/            每次运行的完整归档
├── analysis/           跨运行的分析汇总
└── scratch/            独立调试脚本的临时输出
```

## 1. 在本机保存调用地址和 API Key

打开 `config/runtime.local.json`，在引号中填入 API 调用地址和 Key。两者都已经发在群里。

| 字段        | 填什么                                                 |
| ----------- | ------------------------------------------------------ |
| `api_url` | 智果已部署 Runtime 的完整 HTTPS 调用地址，包含调用路径 |
| `api_key` | API Key 原值，脚本会自动添加 `Bearer `               |

如果文件不存在，可以在编辑器里新建 `config/runtime.local.json` 并从下面的空白结构开始。

```json
{
  "api_url": "",
  "api_key": ""
}
```

保存为 UTF-8。运行时逐项按「环境变量 → 本地文件 → 终端提示」读取。环境变量是 `AGENTARTS_API_URL` 和 `AGENTARTS_API_KEY`。如果文件已经填好但仍调用旧地址，先检查当前终端是否设置了覆盖值。

凭据文件以明文保存在本机，已加入 Git 忽略规则。团队共享脚本或打包归档时，排除 `config/runtime.local.json`，每位同学在自己的副本中填写。

## 2. 单条测试

没有额外输入时，沿用冒烟用例：“设计一种生物医用低模量β钛合金。允许使用Ti、Nb、Zr、Ta、Sn、Mo，禁止使用V、Al、Ni。Ta允许使用但尽量减少。采用wt.%，生成两个候选方案。”

```powershell
python .\run_and_archive.py
```

使用自己的测试用例时，用 `--query` 填需求，用 `--case-id` 标记这条用例。例如：

```powershell
python .\run_and_archive.py --case-id 'no-ta-01' --query '生成两个无Ta的钛合金候选，成分以wt.%表示。'
```

## 3. 批量测试

批量测试文件放在 `cases/` 下，按 `cases_<主题>.jsonl` 命名。`cases.example.jsonl` 是创建新用例文件时可复制的格式模板。例如：

| 文件                                 | 测试内容                                   |
| ------------------------------------ | ------------------------------------------ |
| `cases_missing_requirements.jsonl` | 缺少设计目标或元素范围时，工作流如何处理。 |
| `cases_multiscenario.jsonl`        | 对比医用低模量与工业结构件降成本两种场景。 |

每行写一个 JSON 对象，每条用例使用唯一的 `case_id`。下面是 `cases_missing_requirements.jsonl` 的两条用例。

```jsonl
{"case_id":"no-ta-01","inputs":{"query":"生成两个无Ta的钛合金候选。"}}
{"case_id":"low-modulus-01","inputs":{"query":"生成两个低模量β钛合金候选。"}}
```

以下命令分别运行缺少需求和多场景两组用例，需要运行自己的用例文件时，把 `--cases` 后的路径换成自己的。

```powershell
python .\run_and_archive.py --cases .\cases\cases_missing_requirements.jsonl
python .\run_and_archive.py --cases .\cases\cases_multiscenario.jsonl
```

批量测试会按文件顺序发起调用，每条生成自己的 Run 目录。单条出现已归档的调用或工作流异常时，脚本会继续处理后面的用例。批次有异常时退出码为非零，可用 `$LASTEXITCODE` 查看。批量命令和 `--query` 分开使用。

## 4. 核对归档与版本

每条测试结束后，终端会打印状态和 `archive\tf-...` 目录。常用文件如下。

| 文件                                                 | 用途                                         |
| ---------------------------------------------------- | -------------------------------------------- |
| `request.json`                                     | 本次用例输入、用例 ID 和调用标识             |
| `manifest.json`                                    | 工作流与 Harness 版本、事件数量和采集状态    |
| `nodes.jsonl`                                      | 实际执行的节点及记录到的输入、输出和状态     |
| `raw_events.jsonl`                                 | 收到的原始事件，异常时可能为空或只有部分事件 |
| `run_diagnostics.jsonl`、`failure_records.jsonl` | 运行诊断和异常记录                           |
| `scientific_outcomes.jsonl`                        | 从节点输出提取的科研结果                     |

归档版本默认从 `config/config.json` 读取。比较基线和改进版时，用同一份批量用例分别调用两个版本的实际部署端点。切换调用地址后，同时更新版本配置，或运行时用 `--workflow-version` 和 `--harness-version` 记录本次版本。版本参数只设置归档标签。

## 5. v08 工作流的日志覆盖

- `nodes.jsonl` 记录开始、需求解析、判断、澄清或设计分支、审查、整理和结束节点实际发生的输入、输出和耗时。MCP 节点额外带 `tool_result`，记录工具业务状态、调用 ID、版本和错误字段。原始工具回包仍保留在节点输出中。
- `run_diagnostics.jsonl` 保留平台工作流状态，并记录诊断状态和工具错误数。平台返回成功、MCP 回包返回 `status=error` 时，诊断为 `needs_review`，原因写入 `failure_records.jsonl`，类型为 `tool_reported_error`。例如空文献查询会保留错误码 `EMPTY_QUERY`。文献查无证据和材料待复核属于正常业务结果。
- `scientific_outcomes.jsonl` 提取文献命中、文献待复核、文献无证据，以及候选、守门和审查结果。文献记录保留数据集版本、记录 ID 和证据缺口。物理守门待复核记录为 `gate_pending`。原始调用和从已有原始事件归档时使用同一套处理。
- 日志记录实际发生的事实。是否符合某条测试的预期，由 [Harness](../harness/README.md) 判定。

## 6. 重建历史汇总

在 `logging/` 目录下运行。默认递归读取 `archive/` 中的单次和批量 Run，写入 `analysis/`。也可以用 `--archive-dir` 指向 Run 或批次目录，配合 `--output-dir` 指定新的汇总位置。

```powershell
python -B -m scripts.diagnose_runs
python -B -m scripts.generate_scientific_outcomes
python -B -m tests.test_diagnosis_rules
python -B -m unittest tests.test_v08_archive -v
```
