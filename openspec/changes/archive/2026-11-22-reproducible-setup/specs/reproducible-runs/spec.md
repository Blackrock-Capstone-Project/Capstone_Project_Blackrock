## ADDED Requirements

### Requirement: Clean checkout excludes data and secrets
仓库 MUST 通过 `.gitignore` 排除凭据文件、`Data/` 全部内容、本地虚拟环境、Python/Jupyter 缓存、系统文件以及含行级 WRDS 原值的诊断文件；SHALL 保留代码、依赖说明、测试、事件参考清单、规格与文档。

#### Scenario: Staging all files
- **WHEN** 在仓库根目录执行 `git add -A`
- **THEN** 暂存区不含 `Data/`、`.venv*`、`.env`、`.pgpass`、`__pycache__`、`.DS_Store` 或行级原值诊断文件

### Requirement: Deterministic processing from the same snapshot
相同 raw 快照、配置、`PROCESS_VERSION` 与事件清单 SHALL 得到逐字节相同的数据产物（gzip 头 mtime 固定为 0）；文档 MUST 说明确定性范围（图表 PNG 依赖本机字体渲染，不作逐字节保证；`summary.json` 的 `diff_vs_previous` 取决于目录中已有的上一版本）。

#### Scenario: Independent rebuild
- **WHEN** 在另一个目录用同一 raw 快照重新处理
- **THEN** 处理指纹相同，除图表与 `summary.json` 的上一版本差异字段外各产物 SHA256 与原版本 manifest 一致

### Requirement: Snapshot comparison
系统 SHALL 提供离线只读命令比较两个数据目录的 raw 分区（相同 / 内容改变 / 仅旧 / 仅新，改变分区给出新增、删除键数及逐字段改变行数），以及两个处理版本（规则版本、事件清单指纹、产物指纹、研究样本新增/删除/纳入标志或标签改变行数）。命令 MUST NOT 修改任何输入。

#### Scenario: WRDS revision after re-download
- **WHEN** 新快照中某日 TAQ 分区的某证券竞价量被修订
- **THEN** 比较结果列出该分区为内容改变，并报告 `xvol_*` 等改变的字段与行数
