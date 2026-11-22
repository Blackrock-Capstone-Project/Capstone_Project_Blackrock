## ADDED Requirements

### Requirement: Deferred unresolved securities
状态为 `unresolved` 的证券 SHALL 在本次研究中暂缓使用：`security_quality.csv` 保留其标识、`research_status='deferred'` 与具体原因（`unknown_security_type`、`ticker_history_gap`、`ambiguous_identity`、`missing_effective_listing_market_boundary`、`zero_denominator` 等）。每次处理 MUST 从输入重新评估这些证券，输入满足条件后自动纳入。暂缓证券 MUST NOT 阻止 `process` 或 `update` 报告完成。

#### Scenario: Ticker history gap
- **WHEN** 证券在上市区间内存在无有效 TIC 的交易日
- **THEN** 证券状态为 unresolved，原因为 `ticker_history_gap`，`research_status='deferred'`

#### Scenario: Completion not blocked
- **WHEN** 原始分区完整、无 pending 交易日、处理成功，但存在 unresolved 证券
- **THEN** `quality_status='complete'`，`update` 报告完成，并报告暂缓证券数

### Requirement: Strict PIT eligibility separated from research inclusion
`pit_eligible` SHALL 保持严格 PIT 定义（只用 t 之前数据），MUST NOT 被全窗口证券状态改写。系统 SHALL 另输出 `research_included_expost = eligible` 与 `research_included_pit = pit_eligible AND research_status != 'deferred'`；`research_sample` 只包含二者任一为真的行。

#### Scenario: Deferred security with PIT-eligible days
- **WHEN** 某 unresolved 证券某日 `pit_eligible` 为真
- **THEN** 该行 `pit_eligible` 仍为真，`research_included_pit` 为假，且不进入 `research_sample`

### Requirement: Aligned security status summary
系统 SHALL 输出证券状态对齐表：全部证券 = 非普通股 + 类型不可判定 + 已确认普通股；已确认普通股 = retained + excluded + 已确认普通股中的 unresolved。各项合计 MUST 与 `security_quality.csv` 行数一致。

#### Scenario: Totals reconcile
- **WHEN** 读取对齐表
- **THEN** 每一层合计等于上一层对应数量，最外层等于 `security_quality.csv` 行数
