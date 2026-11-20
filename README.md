# TiForge

TiForge 的 Runtime 日志工具和 Harness 外环是两个同级模块。

| 目录 | 职责 |
| --- | --- |
| [logging/](logging/README.md) | 调用已部署的 Runtime，采集、归档并诊断运行。 |
| [harness/](harness/README.md) | 读取运行归档中的证据，生成待人工复核的 Harness 改进提案。 |

## 运行日志工具

克隆仓库后，从仓库根目录执行：

```powershell
Set-Location .\logging
python -m pip install requests
```

按日志模块 README 配置本机调用地址和 API Key，再运行 `python .\run_and_archive.py`。本机凭据文件位于 `logging/config/runtime.local.json`。

## 运行 Harness 外环

从仓库根目录执行，下例的 `tf-...` 请替换为实际 Run 目录名：

```powershell
python -m harness.propose_change .\logging\archive\tf-...
```

Harness 也可以读取其他位置的运行归档。所需文件和提案格式见 [Harness README](harness/README.md)。
