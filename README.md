# TiForge

TiForge 包含 Runtime 日志工具和 Harness 外环两个模块。

| 模块                         | 职责                                                      |
| ---------------------------- | --------------------------------------------------------- |
| [logging/](logging/README.md) | 调用已部署的 Runtime，采集、归档并诊断运行。              |
| [harness/](harness/README.md) | 配对评测基线与候选运行，输出改进裁决。 |

## 首次克隆

在 PowerShell 中进入准备存放项目的上一级目录，执行以下命令。之后的 Git 命令都从 TiForge 仓库根目录执行。

```powershell
git clone https://github.com/HH-ctrl-lang/tiforge-logging.git TiForge
Set-Location -LiteralPath '.\TiForge'
```

## 同步最新代码

先运行 `git status --short` 查看本地改动。有未提交的工作时，先在自己的分支提交。确认工作区干净后更新主分支。

```powershell
git switch main
git pull --ff-only origin main
```

如果 Git 提示会覆盖本地改动或无法快进合并，保留终端提示，并在群里确认如何处理。

## 提交和推送改动

首次开展任务时，从更新后的 `main` 创建自己的分支，将 `yourname/update-cases` 替换为实际分支名。继续已有任务时，用 `git switch yourname/update-cases` 切回该分支。

```powershell
git switch -c yourname/update-cases
```

从仓库根目录暂存需要共享的文件。按实际改动替换下面的示例路径。

```powershell
git add -- logging/cases/cases_multiscenario.jsonl
git diff --cached --name-status
git ls-files -- logging/config/runtime.local.json
```

提交前核对暂存清单。最后一条命令应没有输出，表示本机 API 凭据文件未被 Git 跟踪。

```powershell
git commit -m "提交说明内容"
git push -u origin HEAD
```

同一分支后续有新提交时，运行 `git push`。推送后在 GitHub 发起 Pull Request，选择 `main` 为目标分支，选择自己的分支作为来源，并通知团队审核。 [GitHub 仓库](https://github.com/HH-ctrl-lang/tiforge)
