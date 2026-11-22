# 待核实与待改进事项

基线：处理版本 `wrds-process-v3`，已归档变更见 [openspec/changes/archive](../openspec/changes/archive/)。每项关闭时注明所在 OpenSpec change 与提交。研究口径见 [数据流程说明](WRDS_DATA_PIPELINE.md)。

## 事件日期（`scripts/data_pipeline/reference/index_events.csv` 中 `verified=false`）

| 执行收盘日 | 事件 | 当前处理 | 待做 | 状态 |
|---|---|---|---|---|
| 2025-06-20 | S&P 季度再平衡 | 按美国方法论第三个周五规则排除 | 查找美国指数官方生效通知（季度股数/IWF 调整或成分变更公告）。已知：S&P 500 当季无成分变化；仅找到 S&P/NZX 官方通知（生效 2025-06-23 开盘） | 未确认 |
| 2026-12-18 | S&P 季度再平衡 | 按规则登记；晚于当前数据截止日，尚不影响产物 | 12 月初 S&P 公告后核对执行收盘日与生效日 | 未来事件 |
| 2026-12-18 | Nasdaq-100 年度重构 | 同上 | 12 月中 Nasdaq 公告后核对 | 未来事件 |

不因"无成分股变化"取消排除；任何日期变更需单独讨论后另开 change。

## 产物与报告

- `report/daily_counts.png` 的 PIT 曲线目前统计严格 `pit_eligible`，研究训练使用 `research_included_pit`（两者仅差 TORO 654 行，单日最多 1 只）。下次规则版本升级时改为同时显示或改用 `research_included_pit`。

## 数据与口径后续项

- 下载 Compustat company 表（SIC/GICS）以标记 REIT（6798）与 SPAC（6770）；当前 `is_reit`、`is_spac` 为 NA。
- WRDS 一次性核验：竞价打印时间戳早于收盘时是否被 `continuous_volume` 重复计入。
- WRDS 一次性核验：Compustat EXCHG `24` 及其 TAQ 场所代码（2026-10 起 ET 等迁出证券）。
- 124 只暂缓证券（105 `unknown_security_type`、19 `ticker_history_gap`）随输入更新自动重新评估；无需手工操作，关注 `report/security_status_summary.csv`。
- 2,257 只证券因全历史异常率 >5% 被事后剔除，约 8.4% 应有股票日缺上市场所竞价标签；按市场与规模的分解尚未做。
