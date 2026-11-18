# TiForge Runtime 测试与日志

如果已经克隆仓库，在 PowerShell 中进入本地仓库目录。首次下载请直接看第 1 节。

```powershell
Set-Location -LiteralPath '你保存的 tiforge-logging 目录路径'
```

## 1. 从仓库拉取最新代码

### 首次下载

在 PowerShell 中进入你想存放项目的上一级目录，然后运行以下命令。

```powershell
git clone https://github.com/HH-ctrl-lang/tiforge-logging.git
Set-Location -LiteralPath '.\tiforge-logging'
python -m pip install requests
```

`git clone` 会创建 `tiforge-logging` 文件夹。进入该目录后，按第 2 节继续。

### 以后同步最新代码

因为版本号、测试输入格式或者日志记录流程可能会有变更，所以大家进行测试前还是都拉取一下最新的代码。

进入已经克隆的 `tiforge-logging` 目录，先运行 `git status --short`。如果有未提交的工作，按第 6 节先在自己的分支提交。确认工作区干净后，再更新 `main`。

```powershell
git switch main
git pull --ff-only origin main
```

如果 Git 提示有本地修改会被覆盖，或无法快进合并，先保留终端提示，并在群里确认如何处理当前改动。需要继续修改代码或用例时，按第 6 节创建或切回自己的工作分支。

## 目录结构

```text
tiforge-logging/
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

参照 `cases/cases.example.jsonl` 创建自己的批量用例文件。下面以 `cases/cases_01.jsonl` 为例，每行写一个 JSON 对象，每条用例使用唯一的 `case_id`。

```jsonl
{"case_id":"no-ta-01","inputs":{"query":"生成两个无Ta的钛合金候选。"}}
{"case_id":"low-modulus-01","inputs":{"query":"生成两个低模量β钛合金候选。"}}
```

```powershell
python .\run_and_archive.py --cases .\cases\cases_01.jsonl
```

想直接试跑仓库自带的示例，可以把 `--cases` 后的路径换成 `.\cases\cases.example.jsonl`。使用其他用例时，填对应的实际文件路径。

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

归档版本默认从 `config/config.json` 读取。比较基线和改进版时，用同一份批量用例分别调用两个版本的实际部署端点。切换调用地址后，同时更新版本配置，或运行时用 `--workflow-version` 和 `--harness-version` 记录本次版本。版本参数只设置归档标签。

## 6. 推送代码到 GitHub

首次提交时创建自己的工作分支，把示例分支名 yourname 换成自己的：

```powershell
git switch -c yourname/update-cases
```

继续已有任务时，用 `git switch yourname/update-cases` 切回对应分支。

把 `git add` 后的路径换成这次要共享的文件。因为仓库是公开的，注意不要上传包含 api 信息的`config/runtime.local.json文件` 。

```powershell
git add -- cases/cases_caseid.jsonl
```

提交前核对暂存列表，第二条命令应没有输出。

```powershell
git diff --cached --name-status
git ls-files -- config/runtime.local.json
```

确认后附上简洁的提交说明。

```powershell
git commit -m "提交说明内容"
```

第一次推送当前分支用下面的命令。之后同一分支有新提交，运行 `git push` 就可以更新它。

```powershell
git push -u origin HEAD
```

推送后打开 [仓库页面](https://github.com/HH-ctrl-lang/tiforge-logging)，点 **Compare & pull request**，或在 **Pull requests → New pull request** 中选择 `base: main` 和你的工作分支。提交请求后告诉我我来检查合并。

## 诊断与分析（正常运行时可忽略）

在日志目录运行。前两条从 `archive/` 的所有 Run 重建 `analysis/` 汇总，最后一条运行模拟诊断测试。

```powershell
python -m scripts.diagnose_runs
python -m scripts.generate_scientific_outcomes
python -m tests.test_diagnosis_rules
```
