# TiForge Harness

Harness 读取 [logging 归档](../logging/README.md)，检查内环工作流的执行结果，比较版本，并根据归档证据提出改动。设计用例也可以启用独立模型评价，逐个检查候选的设计理由和文献引用。

## 代码位置

| 位置                                                                | 职责                                                 |
| ------------------------------------------------------------------- | ---------------------------------------------------- |
| `__main__.py`、`evaluate.py`、`evidence.py`                   | Harness 主命令、单次归档评价和证据导出的入口         |
| `engine/v00.py`、`engine/v01.py`                                | 按 Harness 版本编排采集、评价或版本比较              |
| `core/common.py`、`core/evaluate.py`                            | 读取用例和归档，执行结构、路由及异常分支检查         |
| `semantic.py`、`core/reference_review.py`                       | 整理语义评价材料、调用评价模型、核对引文和结构化主张 |
| `core/propose.py`、`core/agentarts.py`、`core/model_agent.py` | 离线和智能体提案、证据核验及 MaaS 调用               |
| `core/evidence.py`、`core/evidence_mcp.py`、`deploy/`         | 脱敏证据快照、只读 MCP 和部署文件                    |
| `core/snapshot.py`、`core/candidate.py`                         | 保存及比较工作流配置，生成待导入的候选 DSL           |
| `cases/`、`tests/`                                              | 默认用例与 Harness 单元测试                          |

## 用例文件

每行一个 JSON 对象。设计用例模板如下，候选数量、元素清单和语义开关按用例要求填写。

```json
{"case_id":"<用例ID>","inputs":{"query":"<工作流输入文本>"},"expected":{"route":"design","candidate_count":2,"allowed_elements":["<允许元素符号>"],"semantic_review":true}}
```

| 字段                 | 含义                                                                                                                                          |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `case_id`          | 用例文件中唯一的字符串，用来匹配归档                                                                                                          |
| `inputs`           | 提交给工作流的输入对象。评价旧归档时必须与归档请求一致                                                                                        |
| `inputs.query`     | 文本工作流接收的用户需求。其他输入字段按工作流的输入定义填写                                                                                  |
| `expected`         | 用例的固定判据                                                                                                                                |
| `expected.route`   | `design` 为设计，`clarification` 为澄清，`failure_handling` 为异常处理，`reference_review` 为单独评价文献回包或最终回答               |
| `candidate_count`  | 设计用例要求的候选数，填写正整数                                                                                                              |
| `allowed_elements` | 设计用例允许的元素，填写非空字符串数组                                                                                                        |
| `literature`       | 设计用例可选的文献约束。`status` 为预期工具状态，`record_id` 为指定记录，`source_doi` 与记录 ID 配合核对返回的 DOI 及该记录的待复核标记 |
| `semantic_review`  | 设计用例可选的布尔开关。设为 `true` 后纳入独立模型判定，v01 默认套件的设计用例已开启                                                        |

## 运行、复评和对比

```powershell
python -m harness v01 --cases '<用例文件.jsonl>' --run-case '<用例ID>' --workflow-version '<实际部署版本>'
python -m harness v01 --cases '<用例文件.jsonl>' --run-all --workflow-version '<实际部署版本>'
python -m harness.evaluate --engine v01 --cases '<用例文件.jsonl>' --run '<单次归档目录>'
python -m harness v01 --cases '<用例文件.jsonl>' --baseline '<整批归档目录>'
```

| 参数                           | 含义                                                                                                         |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `--cases`                    | 固定用例 JSONL。批量入口省略时使用对应版本的默认套件，单次离线评价需要显式提供                               |
| `--run-case` / `--run-all` | 调用当前智果部署运行指定用例或整套用例                                                                       |
| `--workflow-version`         | 写入归档的版本标签，省略时读取根目录 `config.json` 的 `workflow_version`。实际部署在智果控制台切换并核对 |
| `--run`                      | 单个 `tf-*` 目录，含 `manifest.json`、`request.json` 和节点记录                                        |
| `--engine`                   | 单次评价规则版本，默认 `v01`，可选 `v00`                                                                 |
| `--baseline`                 | 批次目录。单独使用时复评归档，也可写成 `--archive`（v01）                                                  |

在线采集使用 logging 的 Runtime 配置，以终端报告的 `archive_directory` 找到整批归档。单次评价输出 `pass`、`fail`、`needs_review`，退出码依次为 0、1、2。报告中的 `findings`（单次入口为 `issues`）记录问题，其中 `code` 是问题代码、`node` 是节点、`detail` 是依据、`status` 是单项结论。`evidence` 保存支持判定的数据。工作流内的守门待复核表示材料还缺验证数据或判据，Harness 的用例状态取决于该用例预期。工具返回 `no_evidence` 表示该次检索没有可用记录。

批次报告的 `harness_version` 是评测器版本，`workflow_version` 是归档中的工作流版本标签，`suite_sha256` 是用例集指纹，`cases` 按用例 ID 汇总结果，`proposals` 列出从已知失败类型推导出的建议。`run_id` 用于定位该用例的单次运行。单次报告的 `evaluation_version` 是独立评价输出的版本，`engine` 是实际采用的判断规则版本。

在基线和候选部署分别采集同一份用例。先保留基线归档，再切换部署采集候选，最后用两批归档对比。两批需要覆盖指定用例文件中的全部用例，运行 ID 独立且版本不同。比较时固定模型、工具、数据、守门规则及预算等可控条件。

```powershell
python -m harness v01 --cases '<用例文件.jsonl>' --baseline '<基线批次目录>' --candidate '<候选批次目录>' --target-case '<目标用例ID>' --target-finding '<基线中失败的问题代码>'
python -m harness v00 --cases '<历史用例文件.jsonl>' --baseline '<基线批次目录>' --candidate '<候选批次目录>' --target-case '<目标用例ID>' --change '<改动说明>'
```

`--candidate` 为候选归档。`--target-case` 指向本次改动对应的用例，v01 的 `--target-finding` 取自基线中该用例状态为 `fail` 的发现项。`--change` 记录 v00 的改动说明。v01 输出 `decision`，候选有失败时为 `reject`，有待复核时为 `needs_review`，全部通过时为 `ready_for_full_regression`。v00 使用 `accept` 表示通过对照。

### 评价配置与报告

当前 v01 默认套件的设计用例包含结构检查和逐候选的设计理由、文献引用检查。自定义设计用例设置 `expected.semantic_review: true` 以执行完整检查。评价命令会先读取归档中的 `semantic_review.json`，缺少时从本次归档提取评价材料，调用独立模型并将结果写回该次归档。后续复评会核对材料指纹并复用判定。更改用例判据、待评材料或希望重新调用模型时，先另存旧判定文件。

模型调用使用 `deepseek-v4.1-flash-1017`。在项目根目录的 `maas.local.json` 配置 `api_key`，之前已经在群里发过可以直接复制来使用，该文件由 Git 忽略。已设置的 `TIFORGE_MAAS_API_KEY` 优先。

```json
{"api_key":"<MaaS API Key>"}
```

`--offline` 只使用归档中已有的独立判定。已有归档可通过 `--baseline` 或 `harness.evaluate --run` 复评。在在线采集命令上加 `--offline` 时，工作流仍按正常方式调用智果。模型调用失败、判定缺失或引文无法定位时，相应检查进入待复核，已确认的失败仍计入总结果。

| 报告字段                                          | 含义                                                                                                       |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `semantic.packet_sha256`、`semantic.reviewer` | 评价材料指纹及评价者标识                                                                                   |
| `semantic.judgments`                            | 逐候选结论，包含候选 ID、判据、状态、理由和引文定位                                                        |
| `criterion`                                     | `reasoning` 检查设计理由，`citation` 检查文献引用，`evidence_boundary` 检查限定范围的证据使用        |
| `quotes`                                        | `claim_field` 和 `claim_quote` 对应主张字段和原文，`source` 和 `evidence_quote` 对应证据路径和原文 |
| `rationale_composition`、`citation_claims`    | 对支持句式中的配方数值、文献工艺、指标、单位和数值的核对结果                                               |

用例也可将 `expected.route` 设为 `reference_review`，由同一评价命令检查限定范围。`scope` 取 `literature`（文献回包）或 `answer`（最终回答），`criterion` 填本用例固定的文字判据。可选的 `reference_fields` 包含来源 `record_id` 和至少一个要精确核对的字段，可用 `null` 表示来源明确缺失的数值。`allow_empty_records` 仅适用于文献回包，`require_review_summary` 仅适用于最终回答。

### 异常处理用例

`expected.route` 填 `failure_handling`，`checks` 填非空检查数组。`workflow_status` 默认 `succeeded`，需要检查工作流是否按规定停止时可设为 `failed`。

| `checks` 项字段 | 含义                                                                                                                                                            |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `type`          | 异常类型，可选 `hard_constraint_violation`、`out_of_domain`、`prediction_uncertainty`、`insufficient_evidence`、`tool_error`、`insufficient_budget` |
| `node`          | 要核对的节点名                                                                                                                                                  |
| `source`        | 默认读取 `outputs`，也可读取节点的 `error`                                                                                                                  |
| `field`         | 可选的目标字段，省略则检查节点输出或错误对象本身                                                                                                                |
| `where`         | 用于从目标列表字段选中唯一记录的键值条件                                                                                                                        |
| `equals`        | 要逐项匹配的预期键值                                                                                                                                            |
| `required_text` | 要求有非空文字内容的字段名数组                                                                                                                                  |

## 提案、配置快照与候选 DSL

```powershell
python -m harness propose '<归档根目录或批次目录>' --cases '<用例文件.jsonl>' --output '<新提案文件.json>'
python -m harness.evidence --run-id '<运行ID>' --output '<新证据快照.json>'
python -m harness propose --model-agent --case '<用例ID>' --workflow-version '<工作流版本>' --run-id '<运行ID>' --evidence '<证据快照.json>'
```

离线 `propose` 可接收多个归档目录，默认扫描 `logging/archive/`，按版本和问题归组。当前离线规则处理 `EMPTY_QUERY`，没有可验证根因的记录进入 `unresolved`。`--output` 指定要新建的结果文件。证据导出的 `--run-id` 可重复，取自报告中的单次运行 ID。`--archives` 和 `--cases` 可为证据导出指定其他归档根目录和用例文件。

`--model-agent` 从脱敏快照读取证据。`--agentarts` 用于调用已部署的外环提案员，通过 `TIFORGE_PROPOSER_URL` 和 `TIFORGE_PROPOSER_API_KEY` 配置地址和凭证。该模式中已挂载的只读证据 MCP 和本地核验使用同一份证据快照。智能体模式的 `--case` 指定调查的用例，`--run-id` 可限定到某一次运行，`--workflow-version` 默认取根目录配置，`--evidence` 默认取 `logging/archive/harness-evidence.json`。提案报告中的 `proposals` 是经证据核验的建议，`missing_evidence` 是还需补齐的证据。

从智果导出版本 JSONL 后，在本地保存和比较工作流配置。配置差异用于核对部署内容，回归结果来自实际运行归档。

```powershell
python -m harness snapshot save '<基线版本标签>' '<基线原始导出.jsonl>'
python -m harness candidate '<基线版本标签>' '<新候选版本标签>' '<基线原始导出.jsonl>' --case '<用例ID>' --run-id '<基线运行ID>' --evidence '<证据快照.json>'
# 在智果导入、发布并部署后，导出实际部署的候选
python -m harness snapshot save '<候选版本标签>' '<候选实际部署后的导出.jsonl>'
python -m harness snapshot diff '<基线版本标签>' '<候选版本标签>'
```

版本标签对应控制台核对过的实际部署。同名快照和候选目录受覆盖保护。`--directory` 可为 `snapshot` 和 `candidate` 指定快照根目录，默认为 `harness/snapshots/`。快照的 `source` 保留源文件名、哈希和平台版本信息，`definition_sha256` 是归一化配置指纹。差异按节点输出 `node_changes`（提示词、绑定及其他节点配置）和 `workflow_changes`（工作流级配置）。

`candidate` 的三个位置参数依次为已保存的基线标签、新候选标签和与基线哈希一致的原始 DSL。用例、基线运行 ID 和证据快照必须对应。当前自动生成仅覆盖受支持的空查询提示词规则。输出状态 `pending_import` 表示本地候选已生成。`dsl` 指向 `workflow-<新候选版本标签>.jsonl`，`review` 指向同目录的 `review.json`，其中有提案和差异。导入同一工作流 ID 会覆盖智果中的现有配置，导入、发布和部署分别在控制台操作。部署后重新导出实际版本并登记快照，再用相同用例验证。

```powershell
python -B -m unittest discover -s harness/tests
```
