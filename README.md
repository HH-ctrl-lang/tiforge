# TiForge Runtime 测试与日志

在终端进入日志目录。

```powershell
Set-Location -LiteralPath '你保存的目录路径'
```

## 1. 从仓库拉取最新代码分支

仓库创建后补充。

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

## 2. 在本机保存调用地址和 API Key

打开 `config/runtime.local.json`，在引号中填入当前版本的 API 调用地址和 Key。有更新会在群里通知大家。

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

## 3. 单条测试

没有额外输入时，沿用冒烟用例：“设计一种生物医用低模量β钛合金。允许使用Ti、Nb、Zr、Ta、Sn、Mo，禁止使用V、Al、Ni。Ta允许使用但尽量减少。采用wt.%，生成两个候选方案。”

```powershell
python .\run_and_archive.py
```

使用自己的测试用例时，用 `--query` 填需求，用 `--case-id` 标记这条用例。例如：

```powershell
python .\run_and_archive.py --case-id 'no-ta-01' --query '生成两个无Ta的钛合金候选，成分以wt.%表示。'
```

## 4. 批量测试

参照`cases/cases.example.jsonl`示例，创建自己的`cases/cases.jsonl`用例文件。

```jsonl
{"case_id":"no-ta-01","inputs":{"query":"生成两个无Ta的钛合金候选。"}}
{"case_id":"low-modulus-01","inputs":{"query":"生成两个低模量β钛合金候选。"}}
```

```powershell
python .\run_and_archive.py --cases .\cases\cases.jsonl
```

批量测试会按文件顺序发起调用，每条生成自己的 Run 目录。单条出现已归档的调用或工作流异常时，脚本会继续处理后面的用例。批次有异常时退出码为非零，可用 `$LASTEXITCODE` 查看。批量命令和 `--query` 分开使用。

## 5. 核对归档与版本

每条测试结束后，终端会打印状态和 `archive\tf-...` 目录。常用文件如下。

| 文件                                                 | 用途                                         |
| ---------------------------------------------------- | -------------------------------------------- |
| `request.json`                                     | 本次用例输入、用例 ID 和调用标识             |
| `manifest.json`                                    | 工作流与 Harness 版本、事件数量和采集状态    |
| `raw_events.jsonl`                                 | 收到的原始事件，异常时可能为空或只有部分事件 |
| `run_diagnostics.jsonl`、`failure_records.jsonl` | 运行诊断和异常记录                           |
| `scientific_outcomes.jsonl`                        | 从节点输出提取的科研结果                     |

版本默认从 `config/config.json` 读取。比较基线和改进版时，先将 `config/runtime.local.json` 的调用地址切到实际要测试的部署端点，再用同一份 `cases/cases.jsonl` 跑一遍。

## 诊断与分析（正常运行时可忽略）

在日志目录运行。前两条从 `archive/` 的所有 Run 重建 `analysis/` 汇总，最后一条运行模拟诊断测试。

```powershell
python -m scripts.diagnose_runs
python -m scripts.generate_scientific_outcomes
python -m tests.test_diagnosis_rules
```
