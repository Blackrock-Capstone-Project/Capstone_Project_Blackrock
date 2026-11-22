## 1. 纳入集合与原因

- [x] 1.1 `score_grid` 输出 unresolved 具体原因（`zero_denominator` / `ambiguous_identity` / `ticker_history_gap`）
- [x] 1.2 新增 `research_status`、`research_included_expost`、`research_included_pit`；`research_sample` 只含研究纳入行；`pit_eligible` 不变
- [x] 1.3 测试：TIC 缺口证券原因与 deferred；deferred 证券 `pit_eligible` 保留而 `research_included_pit` 为假且不入样本

## 2. 完成状态与对齐

- [x] 2.1 `quality_status` 不再因 unresolved 变为 unresolved；manifest 记录 `deferred_securities`；`update`/CLI 完成判断随之生效
- [x] 2.2 `report/security_status_summary.csv` 与 funnel 更名；测试合计对齐
- [x] 2.3 字段字典与文档写明 `research_sample` 筛选要求

## 3. 事件清单

- [x] 3.1 已核实记录补官方来源、公告日、`verified=true`；Nasdaq 转载来源替换为官方链接（能找到的）
- [x] 3.2 继续查找 2022-03-18、2025-06-20 S&P 美国指数季度调整的官方通知；找不到则保持排除与 `verified=false`
- [x] 3.3 两条 2026-12-18 未来事件保持待确认

## 4. Notebook、重建与核对

- [x] 4.1 notebook 改读 `checkpoints_research`、`research_sample`，展示暂缓证券
- [x] 4.2 `PROCESS_VERSION` 升至 `wrds-process-v3`，全量重建
- [x] 4.3 核对：124 只不在训练样本；状态计数对齐；notebook 单元可读新表；图表已刷新；同输入再运行复用
