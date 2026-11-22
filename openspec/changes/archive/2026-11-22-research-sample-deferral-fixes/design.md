## Context

首次全量运行（`wrds-process-v2`）后的核查结论见 proposal。124 只 unresolved：104 只窗口内无 Compustat 日频；1 只（SLGB）有日频但 `tpci` 缺失；19 只为已确认普通股但上市区间内有无 TIC 的交易日。

## Goals / Non-Goals

**Goals:** 不改变任何研究口径的前提下，修正纳入集合、完成状态、计数对齐、notebook 与图表。
**Non-Goals:** 不改变 5%、10 日 6 有效、PIT 计数、标签或屏蔽日规则；不因 S&P 季度无成分股变化而取消排除。

## Decisions

### D1. 两层字段而不是改写 PIT
`pit_eligible` 是"在 t 日按当时可见数据能否入选"的严格定义；unresolved 是使用全窗口信息的事后判断（TORO 的 TIC 缺口在 2023，PIT 入选行在 2024 之后）。直接把 unresolved 写进 `pit_eligible` 会让 PIT 字段含未来信息。所以新增 `research_status`（证券级）与 `research_included_expost/pit`（行级），训练取后者。**备选**：只从 `research_sample` 过滤而不加列——下游无法在 `stock_day_quality` 上复现同一筛选。

### D2. unresolved 原因细化
`score_grid` 返回 unresolved 时写出原因：`zero_denominator`、`ambiguous_identity`（有歧义匹配日）、`ticker_history_gap`（只有 missing_identity 日）。原有 summary 级原因（`unknown_security_type` 等）不变。

### D3. 完成状态
`manifest.quality_status` 只反映处理是否成功（`complete`），另记 `deferred_securities`。`update` 的完成仍要求原始数据完整且无 pending 交易日（WRDS 发布滞后属于数据未齐，单独显示）。CLI `process` 以 `quality_status=='complete'` 返回 0。

### D4. 状态对齐表
`report/security_status_summary.csv` 三层：全部 → 非普通股 / 类型不可判定 / 已确认普通股；已确认普通股 → retained / excluded / unresolved。funnel 的 `common_stock` 更名 `confirmed_common_stock`。

### D5. 版本
`PROCESS_VERSION` → `wrds-process-v3`，统一重建，同时刷新图表。

## Risks / Trade-offs

- [下游误用 `pit_eligible` 作训练筛选] → 字段字典与文档写明训练用 `research_included_*`。
- [重建约 15 分钟、峰值内存约 22 GB] → 后台运行。
