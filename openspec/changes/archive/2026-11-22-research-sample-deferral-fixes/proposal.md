## Why

`closing-auction-processing` 首次全量运行后核查发现：124 只 unresolved 证券永久阻止 `update` 完成；其中 1 只（TORO）因 `pit_eligible` 不看证券状态而进入 `research_sample`；funnel 的 `common_stock` 标签与状态汇总无法对齐；notebook 仍读取已删除的 `checkpoints_clean`；图表修复因未升版本未进入产物；25 条事件记录尚未逐条对照官方来源。

## What Changes

- 区分**严格 PIT 资格**（`pit_eligible`，只用 t−1 以前数据，不受全窗口状态影响，保持不变）与**本次研究纳入条件**（新增 `research_included_expost`、`research_included_pit`，排除本次暂缓的 unresolved 证券）。`research_sample` 只含研究纳入行。
- unresolved 证券改为"暂缓使用（deferred）"：保留标识与具体原因（`unknown_security_type`、`ticker_history_gap` 等），每次处理重新评估；不再阻止 `update`/`process` 完成。
- 新增证券状态对齐表，funnel 标签更正。
- notebook 改读新表；`PROCESS_VERSION` 升级并统一重建，刷新图表。
- 事件清单：已核实记录补官方来源、公告日与验证状态；两条未直接确认的 S&P 历史日期保持排除且 `verified=false`；两条未来事件保持待确认。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

无主规格（两个前序变更均未归档）。本变更对 `closing-auction-processing` 的 `auction-research-dataset` 规格追加要求，写在 `specs/auction-research-dataset/spec.md` 的 ADDED 部分。

## Impact

`scripts/data_pipeline/{quality,processing,report,dictionary,common}.py`、`reference/index_events.csv`、`scripts/download/download_wrds.ipynb`、测试与文档；`Data/processed/` 全量重建一次。不改变 ex-post 规则、PIT 规则、标签或屏蔽日口径。
