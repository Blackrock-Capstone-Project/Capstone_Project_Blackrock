## Context

基线：提交 `b50515d`（tag `baseline-wrds-process-v3`），处理版本 `.wrds_versions/4eb19f3a851541f5ba1d14a1dc7c445c`。

## Goals / Non-Goals

**Goals:** 结构简洁、每个模块一个流程阶段；函数体原样迁移；输出逐字节一致。
**Non-Goals:** 不改规则、不升版本、不改 `Data/` 布局。

## Decisions

- **D1 迁移方式**：以 AST 提取原函数源码迁入新模块，只改 import，避免手工改写引入行为差异。
- **D2 模块映射**：`common` → `settings`（版本、常量、错误类）+ `trading_calendar`（不用 `calendar` 以免与标准库同名）+ `storage`（含原 `processing.verify_table`、`output_valid`、确定性 gzip）；`auth` → `wrds_auth`；`sources`+`taq_sql` → `wrds_queries`；`pipeline` 保持为三个公开入口 update/process/audit 与分区计划（原方案拆成 `download`，但 process 需要 raw 审计、update 需要调用 process，拆开会循环依赖）；`quality` → `identity`（符号键、证券类型、逐证券上市网格与匹配）+ `labels` + `eligibility`；`processing` → `build`（日频清洗、逐证券流式、检查点）+ `publish`（版本目录、表写入与校验、指针切换）。
- **D3 测试**：`tests/helpers.py` 提供合成数据与模拟 WRDS 的 mixin（不继承 `TestCase`，避免重复发现）；测试按模块分文件，用例逐条保留。
- **D4 删除清单**：旧脚本、其 8 个测试、`results/`、`docs/US_WRDS_DAILY_PIPELINE.md`、`docs/WRDS_MIGRATION.md`。这些仅存在于基线提交历史。
- **D5 一致性证明**：新代码对同一 raw 快照在独立目录重建，`compare` 对基线版本：指纹相同、数据产物逐字节一致（图表与 `summary.json.diff_vs_previous` 按复现文档排除）。另验证真实 `Data/` 上 `process` 复用、`audit` 完成、`check --help`、notebook 单元。

## Risks / Trade-offs

- [拆分 `publish` 改变写出顺序或格式] → 一致性比较为准，任何差异阻止发布。
- [下游脚本引用旧路径] → 仓库内全文检索 `scripts.`、`scripts/` 清零。

## 实施记录

- notebook 离线单元验证时发现既有缺陷：处理单元按 `info["rows"]` 列出 manifest 文件，而自 v2 起报告文件只记录 `sha256`，导致 `KeyError`。改为 `info.get("rows")`；不影响任何数据产物。
- 用新代码在真实 `Data/` 上运行 notebook 处理单元：处理指纹 `cc72b366…` 与基线相同，`reused: True`，`wrds_current` 未变。
- 前后一致性：新代码对基线 raw 快照（1,286 分区）在独立目录重建 994 秒，与基线版本 `4eb19f3a…` 比较：处理指纹相同、事件清单相同、输入分区无差异，21 个数据产物 SHA256 逐字节一致；`summary.json` 仅 `diff_vs_previous` 不同（独立目录无上一版本），图表按复现文档跳过比较（本机实际也一致）；manifest 仅 `summary.json` 的文件指纹不同，文件顺序一致。
