## Why

代码分散在 `scripts/` 下两代实现中：旧下载/处理/诊断脚本（约 600 行，依赖 `sys.path` 拼接）仍像正式入口；现行模块边界混杂（`quality.py` 同时做身份匹配、标签与资格，`processing.publish` 同时构建、写盘、校验、报告和切换版本，`common.py` 混合版本、日历、IO 与锁）；依赖文件与 notebook 位置不直观；历史 `results/` 不由现行代码产生。需要简洁直白的结构，并在不改变任何数据口径的前提下证明输出一致。

## What Changes

- **BREAKING**：包改为仓库根目录 `auction_data/`，入口 `python -m auction_data update|process|audit|compare|check`；不保留 `python -m scripts.data_pipeline` 及旧脚本入口。
- 按流程阶段拆分模块：settings / trading_calendar / storage / wrds_auth / wrds_queries / pipeline / identity / labels / events / eligibility / splits / build / publish / report / dictionary / compare。
- `check` 子命令取代 `wrds_taq_check.py`（只读权限与发布日期探测）。
- 删除旧脚本 `scripts/download/{wrds_daily_pull,taq_auction_pull,wrds_taq_check}.py`、`scripts/processing/*`、其测试、历史 `results/`、重定向文档与旧迁移记录。
- 测试移至 `tests/`，以共享 helpers 取代子类置空技巧；依赖移至根目录 `requirements.txt`；notebook 移至 `notebooks/`；参考报告移至 `docs/reference/`。
- 新增详尽数据报告 `docs/DATA_REPORT.md`。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `reproducible-runs`：入口与文件位置变化后，干净 checkout 的复现步骤与一致性验证要求更新。

## Impact

不改变 `RAW_VERSION`（wrds-v1）或 `PROCESS_VERSION`（wrds-process-v3）及任何规则；`Data/` 布局、raw 分区、处理产物路径与 manifest 不变。验证：同一 raw 快照用新代码在独立目录重建，与基线版本处理指纹相同且数据产物逐字节一致。
