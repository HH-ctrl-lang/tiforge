# TiForge Harness

Harness 读取 [logging 归档](../logging/README.md)，按用例检查已部署工作流，输出问题证据和版本对照结果。

## 目录结构

| 位置                                     | 内容                                                                 |
| ---------------------------------------- | -------------------------------------------------------------------- |
| `core/common.py` | 读取用例和归档，校验批次，提取节点字段，检查候选成分 |
| `core/evaluate.py` | 单次和批量共用的评价器，包含 v00、v01 和异常处理判据 |
| `core/propose.py`、`core/agentarts.py` | 生成离线提案，或调用 AgentArts 并核对智能体所引用的证据 |
| `core/evidence.py`、`core/evidence_mcp.py` | 导出脱敏证据及提供只读 MCP 服务 |
| `engine/v00.py`、`engine/v01.py` | 版本编排，负责调用共用逻辑、生成报告和对照结果 |
| `deploy/` | 证据 MCP 的镜像配置和运行依赖 |
| `cases/`、`tests/` | 各版本的默认用例和 Harness 单元测试 |
| `__main__.py`、`evaluate.py`、`evidence.py` | 命令入口，实际逻辑在 `core/` 和 `engine/` |

默认用例集中在 `cases/`。自定义用例可通过 `--cases` 指定。

## 版本与用途

| 版本 | 默认用例                              | 检查范围                                                     | 对照结果                                                    |
| ---- | ------------------------------------- | ------------------------------------------------------------ | ----------------------------------------------------------- |
| v00  | 3 条，医用、工业、Nb 冲突             | 路由、候选数量和配方、旧版布尔守门                           | `accept`、`reject`、`needs_review`                    |
| v01  | 4 条，Ti001、Ti999、工业降本、Nb 冲突 | 路由、文献检索与传递、配方、物理守门三态、审查和科研结果归档 | `ready_for_full_regression`、`reject`、`needs_review` |

v08 使用 v01。原 v00 的设计用例需要 `all_passed=true`，新版物理守门待复核会被其判失败。v01 核对基础成分和逐候选守门事实，预期的待复核可以通过结构检查。v01 的修改建议按问题代码查预设规则，由团队据此准备实际改动。

## 运行和对照

只跑一条用例时，在仓库根目录运行：

```powershell
python -m harness v01 --run-case industrial-cost
```

命令从项目根目录的 [config.json](../config.json) 读取工作流版本，用本机 Runtime 配置调用智果，将本次归档保存到 TiForge 的 `logging/archive/harness-v01-.../`，并输出 Harness 报告。命令中的 `v01` 选择本次使用的评测器。把 `industrial-cost` 换成 `cases/v01.jsonl` 里的其他用例 ID 就能换测另一条；临时切换工作流版本标签可使用 `--workflow-version`。

### 批量运行

```powershell
python -m harness v01 --run-all
```

命令依次运行默认的 4 条开发用例，并把整批运行保存在 `logging/archive/` 下的一个新目录中。终端报告里的 `archive_directory` 是整批归档的路径。要从已有归档重新评测，可以运行 `python -m harness v01 --baseline '<归档目录>'`。

### 对照两个部署

对基线和候选部署各执行一次 `--run-all`，再把两次报告的归档目录填进下面的命令。比较时固定用例、模型、守门规则、工具及数据版本和预算，并记录实际部署标识。

```powershell
python -m harness v01 --baseline '<基线归档目录>' --candidate '<候选归档目录>' --target-case industrial-cost --target-finding literature_lookup
```

目标问题在基线有证据，且候选批次全通过时，可继续跑团队提供的完整固定基准。两侧必须有各自独立的运行，使用同一份用例。

评测历史使用 v00 时，两批归档应使用 `cases/v00.jsonl` 采集。

```powershell
python -m harness v00 --baseline '<基线归档目录>' --candidate '<候选归档目录>' --target-case nb-conflict-01 --change '修复 Nb 冲突时的澄清输出'
python -B -m unittest discover -s harness/tests -v
```

## 保存和比较工作流配置

在智果的工作流列表导出同一条工作流的 AgentArts V2.0 JSONL。每份导出对应一个已确认的版本标签。在 TiForge 根目录运行：

```powershell
python -m harness snapshot save v08 '<智果导出的 v08 JSONL>'
python -m harness snapshot save v09 '<智果导出的 v09 JSONL>'
python -m harness snapshot diff v08 v09
```

命令按节点 ID 保存提示词、输入绑定、输出定义、节点配置和连线，保留源文件哈希、配置哈希和导出里的平台版本标识。对比输出提示词逐行差异、输入修改前后的值，以及其余节点配置和连线的差异。版本标签由操作者填写，需用控制台核对出包对应的实际部署。

快照默认写入 Git 忽略的 `harness/snapshots/`，同名版本文件不会覆盖。原始导出可能包含关联资源的鉴权配置，快照仅保留这些资源的引用标识，并遮盖工作流中的已知凭证字段。

### 生成待导入的候选 DSL

```powershell
python -m harness candidate v08 v09 '<原始 v08 JSONL>' --case industrial-cost --run-id tf-20261126-194923-da3b059e28d9
```

候选命令核对原始导出和基线快照，调用 MaaS 提案员核实空查询并生成指定提示词行的替换文本。当前支持研究需求解析的空查询规则。它生成 `harness/snapshots/candidates/v09/workflow-v09.jsonl` 和用于审核的 `review.json`。候选文件保留原工作流 ID 和关联资源，审核后可在智果导入预览。选择同 ID 工作流会覆盖其现有配置，导入和部署分开确认。发布后重新导出实际版本，再用 `snapshot save` 登记。 [智果 DSL 导入](https://support.huaweicloud.com/lowcode-agentarts/agentarts_05_0058.html)

## 从已有归档生成改动提案

```powershell
python -m harness propose
```

默认扫描 `logging/archive/`，用当前 v01 的用例和独立评价器重评归档。可以指定一个或多个归档目录，例如 `python -m harness propose ./logging/archive '<历史归档目录>'`。要保留提案清单，可加 `--output '<新的 JSON 文件路径>'`。

当前处理 `EMPTY_QUERY`。程序沿需求解析输出、MCP 输入和错误回包定位最早的可见偏差，按版本和偏差类型合并重复运行。每条提案包含 Run ID、证据字段、建议改文、预期修复的用例和回归风险。相同用例的通过记录优先作为参考，其他通过的设计用例仅用于说明接口行为。未找到可验证根因的失败和采集中断保留在 `unresolved`。

这一实现参考 HarnessEvolve 的首处偏差定位与同源错误归组，以及 AHE 的改动清单和可检验预期。提案是待评审草案，候选版本应用和上线由后续验证决定。 [HarnessEvolve 方法](https://arxiv.org/html/2609.00829v1#S3.SS4) · [AHE 方法](https://arxiv.org/html/2604.25850v3#S3.SS3)

### 直接运行外环提案员

本机执行只读证据工具，调用华为云 MaaS 的 DeepSeek-V4.1-Flash-1017 选择工具和生成提案。Harness 继续核对实际工具返回的证据。此模式从同一份脱敏快照取证，无需部署 MCP 容器。

```powershell
python -m harness propose --model-agent --case industrial-cost --run-id tf-20261126-194923-da3b059e28d9
```

在 TiForge 根目录的 `maas.local.json` 中填写西南-贵阳一的 **MaaS API Key**，格式为 `{"api_key": "你的 MaaS API Key"}`。该文件已加入 Git 忽略规则。如果设置了 `TIFORGE_MAAS_API_KEY`，程序优先使用环境变量。两处都没填写时可以在交互式终端输入。

证据快照默认为 `logging/archive/harness-evidence.json`，可用 `--evidence` 指定。若尚未导出快照，使用下一节的证据导出命令。 [华为云 MaaS 模型接口](https://support.huaweicloud.com/model-call-maas/model-call-019.html)

### 接入 AgentArts 提案员

在项目根目录导出联调用的两条 v08 记录。命令生成 `logging/archive/harness-evidence.json`，并保护已有文件。每次新增证据时，把重新导出的同一份快照用于 MCP 镜像和本地核验。

```powershell
python -m harness.evidence --run-id tf-20261126-194923-da3b059e28d9 --run-id tf-20261126-172736-80e955cb52d7
```

#### 在云端构建 MCP 镜像

使用 CodeArts Build 的图形化构建时，选择包含本次代码的分支，构建环境选 **ARM（鲲鹏）**。在 **制作镜像并推送到 SWR 仓库** 前添加 Python 构建步骤，在代码仓根目录运行上面的证据导出命令。两条源归档已纳入版本控制，导出的快照在构建过程中生成。SWR 步骤的工作目录为代码仓根目录，Dockerfile 路径填写 `./harness/deploy/Dockerfile`，构建上下文路径填写 `.`。本机只需保留同一份快照供 CLI 核验。 [CodeArts 构建环境](https://support.huaweicloud.com/usermanual-codeci/codeci_ug_1047.html) · [制作镜像并推送到 SWR](https://support.huaweicloud.com/usermanual-codeci/codeci_ug_0013.html)

有 Docker Buildx 的构建机也可在仓库根目录执行以下命令。先登录 SWR，并把镜像名换成实际地址。

```powershell
$image = '填写SWR完整镜像地址并保留标签:v01'
docker buildx build --platform linux/arm64 -f harness/deploy/Dockerfile -t $image --push .
```

#### 部署 MCP 和发布提案员

在 AgentArts 的 **托管与运行 → 智能体运行时** 创建 MCP 运行时，选择该镜像、**入站协议 MCP** 和 **入站身份认证 API Key**。镜像监听容器内的 `0.0.0.0:8000/mcp`，外部访问由运行时网关鉴权。复制运行时详情中的完整访问地址和 Key。 [MCP 运行时部署](https://support.huaweicloud.com/highcode-agentarts/agentarts_10_127.html) · [MCP 运行时访问与鉴权](https://support.huaweicloud.com/highcode-agentarts/agentarts_10_067.html)

在智果 **MCP → 创建 MCP → 自定义接入** 选择 **HTTP → Streamable HTTP**，粘贴运行时的完整访问地址。鉴权选择 API Key，位置选 Header，参数名为 `Authorization`，参数值为 `Bearer <MCP API Key>`。安装后，在已配置的单智能体中添加该 MCP。调试时按用例 `industrial-cost`、版本 `v08` 查询，确认有 `list_runs`、`read_run_evidence` 调用。第一条运行应读到需求解析的空查询和文献节点的 `EMPTY_QUERY`，第二条应因连接中断返回 `needs_review`。 [自定义 MCP 接入](https://support.huaweicloud.com/lowcode-agentarts/agentarts_05_0136.html) · [挂载和调试 MCP](https://support.huaweicloud.com/lowcode-agentarts/agentarts_05_0138.html)

将同一个单智能体提交版本并部署至实例，入站认证选择 API Key。把**单智能体**的调用路径和 Key 分别设置为本机进程环境变量 `TIFORGE_PROPOSER_URL`、`TIFORGE_PROPOSER_API_KEY`。证据 MCP 使用上一步单独配置的运行时凭证。 [单智能体部署和调用](https://support.huaweicloud.com/bestpractice-agentarts/agentarts_06_0021.html)

```powershell
python -m harness propose --agentarts --case industrial-cost --run-id tf-20261126-194923-da3b059e28d9 --output logging/archive/industrial-cost-proposal.json
```

工作流版本默认取项目根目录的 `config.json`，`--workflow-version` 可覆盖。省略 `--run-id` 时由智能体在当前用例和版本内选取归档。CLI 读取本次 AgentArts 调试流的 `plugin_end` 和最终 JSON，校验证据内容和目标字段。字段绑定快照目前缺失，绑定类提案需人工复核。证据快照只包含批准的字段，目前覆盖查询是否为空、分支布尔值、节点状态和已知错误码。可扩充字段范围以支持其他诊断。使用 `--evidence` 可指定本地用于核验的快照。

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
用例集须按具体节点实际提供的结构化字段来写。单次评价和 Harness 批量评测使用同一套判据，两种入口均可用 `--cases` 指定自定义失败分支用例集。
