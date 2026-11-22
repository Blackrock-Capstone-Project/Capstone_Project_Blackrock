## ADDED Requirements

### Requirement: Common stock universe
研究样本 SHALL 只包含 Compustat `tpci='0'` 的证券；`%`、`1`、`G`、`2`、`4`、`7`、`F`、`S`、`R` 及其他代码 MUST 排除并在观察层记录排除原因。带分类后缀的普通股 MUST 以 Compustat `tic` 的 `.` 分隔部分对应 TAQ `sym_root` 与 `sym_suffix` 精确匹配。`is_reit` 与 `is_spac` 列 SHALL 存在且为 NA，直到有可验证的行业来源。清单 MUST 注明 `tpci` 为当前快照而非逐日历史值。

#### Scenario: Class share mapping
- **WHEN** Compustat `tic='BRK.B'`、`tpci='0'` 且 TAQ 有 `sym_root='BRK'`、`sym_suffix='B'`
- **THEN** 两者匹配为同一证券日

#### Scenario: Debt-like code R
- **WHEN** 证券 `tpci='R'`（如 CCZ、TVC）
- **THEN** 该证券不进入研究样本，排除原因为非普通股

### Requirement: Listing venue auction label
每个证券日的标签 SHALL 为当日历史上市交易所的收盘竞价股数：EXCHG=11 取 `xvol_n`，EXCHG=14 取 `xvol_q`。对应字段为空时标签 MUST 为 NA 且 `label_status='listing_venue_cross_missing'`，MUST NOT 用其他场所或 `auction_volume` 回退。原 `auction_volume`、`listing_ex`、`auction_volume_all`、`xvol_*`、`auction_price`、`n_prints`、`trf_close_volume` SHALL 保留。

#### Scenario: Transfer between target markets
- **WHEN** 证券 EXCHG 于 2023-05-22 由 14 变为 11（如 OBK）
- **THEN** 此前标签取 `xvol_q`、此后取 `xvol_n`，`security_id` 与历史窗口不中断

#### Scenario: Cross on another venue
- **WHEN** 上市交易所为 NYSE，`xvol_n` 为空而 `xvol_p` 有值
- **THEN** 标签为 NA，对账分类为场所冲突

#### Scenario: Cross outside known venues
- **WHEN** `auction_volume_all > 0` 而 `xvol_n/q/p/a/z` 全为空
- **THEN** 该证券日列入"竞价在已知场所之外"报告

### Requirement: Listing interval boundaries
证券的应有交易日 SHALL 只来自 EXCHG∈{11,14} 的生效区间。EXCHG 变为其他代码时，该证券在变更日退出应有交易日网格，此后 MUST NOT 计为异常。

#### Scenario: Move to exchange code 24
- **WHEN** ET 的 EXCHG 于 2026-10-05 由 11 变为 24
- **THEN** 2026-10-05 起 ET 不在应有交易日网格内，其缺失标签不计入异常率

### Requirement: Event day exclusion
系统 SHALL 维护版本化的重大指数再平衡执行日清单，覆盖 S&P DJI 季度再平衡、Nasdaq-100 年度重构、Russell 美国指数重构、MSCI 季度及半年度评审；每条记录 MUST 含事件族、指数、公告日、执行收盘日、生效日、适用市场、官方来源及核验状态。执行收盘日与 XNYS 半日市 SHALL 退出研究样本、滚动窗口、检查点预期量估计及 5% 分母；同一市场日期多个事件只剔除一次。单股临时调整 MUST NOT 触发市场级剔除。

#### Scenario: Multiple events same day
- **WHEN** 12 月第三个周五同时为 S&P 季度再平衡与 Nasdaq-100 重构
- **THEN** 清单保留两条明细，样本中该日只剔除一次

#### Scenario: Half day checkpoints
- **WHEN** 会话为 13:00 收盘的半日市
- **THEN** 观察层保留 10:00、12:00 检查点，其余检查点为 `not_applicable`，该日不进入研究样本

#### Scenario: Unverified event date
- **WHEN** 清单中某日期未经官方来源核验
- **THEN** 质量报告列出该条目，清单状态为未完成核验

### Requirement: Ex-post eligibility with event masking
有效股票日 SHALL 要求 Compustat `cshtrd` 与上市场所标签均有限且 > 0。整股异常率分母 SHALL 为请求范围 ∩ 目标市场上市区间 ∩ 两来源共同已验证覆盖，减去事件日、半日市与 pending 日；完整分区内个股缺记录计异常，来源级缺分区不计。异常率严格 > 5% 整股剔除，恰好 5% 保留，分母为 0 或边界无法核验为 `unresolved`。逐日资格 SHALL 取不含当日的前 10 个 XNYS 会话，屏蔽事件日与半日市后不向前补足，至少 6 个有效日且当日有效。

#### Scenario: Event day inside window
- **WHEN** t 日前 10 个会话中有 1 个事件日，其余 9 个中有 6 个有效
- **THEN** t 日满足窗口条件

#### Scenario: Window too thin after masking
- **WHEN** 屏蔽后前 10 个会话中只有 5 个有效日
- **THEN** t 日不合格，原因为历史有效日不足

#### Scenario: Five percent boundary
- **WHEN** 分母 100 天，异常 5 天
- **THEN** 证券通过整股筛选；异常 6 天时整股剔除

### Requirement: Point-in-time eligibility
系统 SHALL 为每个证券日输出 `pit_n_expected`、`pit_n_anomaly`（只计 t−1 及以前，在 `[max(请求起点, 上市区间起点), t−1]` 上）及 `pit_eligible`（扩展窗口异常率不超过 5%、10 日 6 有效且当日有效）。PIT 字段 MUST NOT 使用 t 日或以后的数据；ex-post 字段 SHALL 独立保留。

#### Scenario: Future anomaly does not leak
- **WHEN** 证券在 t+30 日出现大量异常使全历史异常率超过 5%
- **THEN** t 日的 `pit_n_anomaly` 与 `pit_eligible` 不变，ex-post 资格变为剔除

#### Scenario: Early listing small denominator
- **WHEN** 新股上市第 12 个会话时已有 1 个异常日
- **THEN** `pit_eligible` 为假，报告统计此类暂时剔除的股票日数

### Requirement: Split share conversion
系统 SHALL 保留原生股数与 `ajexdi`，并以 `vol_s × ajexdi_s / ajexdi_t` 把历史量换算到预测日 t 的股本口径；同日量不换算。`ajexdi` 变化日的价格前后比与因子比偏差超过 20%、因子缺失或非正时，证券 MUST 标为 `split_unresolved`，跨该日期的换算值为 NA。无因子变化但价格或股数跳变超过阈值的证券日 SHALL 列入疑点报告。

#### Scenario: Forward split
- **WHEN** NVDA 2024-06-07 `ajexdi=10`、成交量 40.9M，预测日 2024-06-12 `ajexdi=1`
- **THEN** 换算后历史量为 409M 股

#### Scenario: Later split does not change ratio
- **WHEN** 同一数据在之后的快照中所有 `ajexdi` 同乘常数
- **THEN** 换算结果不变

#### Scenario: Inconsistent factor
- **WHEN** 因子比为 10 而收盘价前后比为 3
- **THEN** 证券标为 `split_unresolved`，跨该日的换算历史量为 NA

### Requirement: Intraday checkpoints with availability
系统 SHALL 输出以 `(security_id, date, checkpoint)` 为键的检查点长表，正常交易日包含 10:00、12:00、14:00、15:00、15:30、15:50，保留小数并标记非有限、负值和累计量倒退。字段字典 MUST 为每个字段标注可用时点：`t-1 close`、`t intraday`、`t post-close`；当天竞价量、竞价价、打印次数、`continuous_volume`、`trf_close_volume` 与 `cshtrd` 为 `t post-close`，以其为分母的比例 MUST 以 `_diag` 结尾。

#### Scenario: Post-close field
- **WHEN** 下游请求 t 日 15:50 可用字段
- **THEN** 字段字典不将 `continuous_volume` 或竞价量列为可用

### Requirement: Deterministic versioned publication
同一输入与规则版本重复运行 SHALL 产生相同输出指纹并复用现有版本；规则版本或输入变化 SHALL 全区间重建，在读盘校验主键和行数后原子切换 `wrds_current`，并记录与上一版相比新增、改变、删除的股票日数。

#### Scenario: Repeat run
- **WHEN** 输入和规则未变时再次处理
- **THEN** 输出指纹一致，不生成新版本

### Requirement: Quality report
系统 SHALL 输出质量报告，至少包含：每日保留股票数时间序列及底表（区分保留、事件日、半日市、来源缺口）；处理前后证券数、股票日数、检查点数；固定顺序筛选漏斗（身份匹配 → 普通股 → 上市区间 → 事件日/半日市 → ex-post 5% → 10 日 6 有效 → 当日有效）；5% 剔除名单；标签对账四类（一致、场所冲突、上市场所竞价缺失、身份未决）；已知场所外竞价名单；`split_unresolved` 与跳变疑点；事件清单核验状态；PIT 与 ex-post 资格不一致比例。

#### Scenario: Funnel without double counting
- **WHEN** 某股票日既是事件日又当日异常
- **THEN** 漏斗只在事件日步骤计数一次
