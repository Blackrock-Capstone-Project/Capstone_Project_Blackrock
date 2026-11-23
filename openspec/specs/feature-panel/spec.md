# feature-panel Specification

## Purpose
从固定的 processed 版本只读生成时点一致的特征面板：日频核心特征、盘中检查点特征与月度行为画像（不拟合），附目标与筛选元数据、特征字典、不可改写的版本与质量报告。口径与使用说明见 `docs/FEATURES.md`。
## Requirements
### Requirement: Pinned read-only processed input
特征构建 SHALL 在开始时把 `Data/processed/wrds_current` 解析为实际版本目录（或使用 `--processed-dir` 指定的版本目录），此后只从该目录读取 `stock_day_quality`、`daily_clean`、`checkpoints_research` 与 `manifest.json`。读取前 SHALL 用 processed manifest 中的 sha256 校验三个输入文件；构建结束时 SHALL 再次确认 processed 指纹与输入文件摘要未变，否则失败且不发布。系统 MUST NOT 修改 `auction_data` 代码或 `Data/processed/` 下任何文件，MUST NOT 读取 raw 分区。特征 manifest SHALL 记录解析后的目录、processed 版本与指纹及输入文件摘要。

#### Scenario: Current pointer switches during build
- **WHEN** 构建进行中 `wrds_current` 被切换到另一个版本
- **THEN** 本次构建仍只读取开始时解析出的版本目录，manifest 记录该目录

#### Scenario: Input digest mismatch
- **WHEN** `stock_day_quality.csv.gz` 的 sha256 与 processed manifest 不一致
- **THEN** 构建失败，不写入 `Data/features/current`

#### Scenario: Processed outputs unchanged
- **WHEN** 运行 `python -m auction_features build`
- **THEN** 构建前后 processed 指纹与三个输入文件 sha256 相同

### Requirement: Full-panel history with masked placeholders
历史特征 SHALL 在 `stock_day_quality` 全部上市会话上计算，窗口按 XNYS 会话计数，屏蔽日与未上市会话占位且不回补、不向前延伸。`research_included_pit`、`research_included_expost`、`research_status`、`security_status`、`eligible` MUST NOT 影响任何特征值。daily 输出行集合 SHALL 与 `stock_day_quality` 主键集合相同。

#### Scenario: Window not compressed by inclusion flags
- **WHEN** 某证券 t−3 为非纳入行（`research_included_pit` 为假）但 `valid_day` 为真
- **THEN** 该观测参与 t 日 `a_ma5`，t 日窗口仍为 t−5…t−1 五个会话

#### Scenario: Inclusion flags do not change features
- **WHEN** 将全部研究纳入标志与证券状态改为任意值后重算
- **THEN** 所有特征与画像逐位不变

#### Scenario: Event day occupies a slot
- **WHEN** t−2 为事件日，其余 4 个会话 U_A 成立
- **THEN** `a_ma5` 用 4 个观测，窗口不延伸到 t−6

### Requirement: Observation usability and continuous market intervals
历史观测 s SHALL 按族判定可用：竞价族要求 `valid_day_s`；成交量族要求上市、未屏蔽且 `PX_VOLUME_s` 有限 >0；检查点族要求 `applicable`、`volume_valid` 且未屏蔽。这三族 MUST 同时要求 s 与 t 处于同一连续市场区间（连续上市且 `market` 不变；未上市会话或 `market` 变化开始新区间），且 `seg_s = seg_ref_t`（t−1 及以前最后已知分段）。价格族要求 `PX_LAST_s` 有限 >0 且允许屏蔽日。缺失 MUST 保持 NaN，MUST NOT 以 0 填充。

#### Scenario: Price on event day
- **WHEN** t−1 为事件日且有收盘价与股本
- **THEN** `logmcap_lag1` 使用 t−1 的值，`d_lag1` 为 NaN

#### Scenario: Market transfer starts a new interval
- **WHEN** 证券于 t−3 由 NASDAQ 转为 NYSE
- **THEN** t 日竞价与成交量窗口只使用 t−3 起的观测，t−3 行 `interval_start` 为真

#### Scenario: Return to an earlier market
- **WHEN** 证券 NYSE → NASDAQ → NYSE，t 位于第二段 NYSE 区间
- **THEN** 第一段 NYSE 区间的观测不进入 t 的任何窗口

#### Scenario: Relisting gap
- **WHEN** 证券在 t−30…t−11 未上市，此前与此后均为 NYSE
- **THEN** t 的窗口不使用 t−31 及以前的观测

### Requirement: Forecast-time split consistency and missing factors
历史股数 SHALL 换算为 `V_s·f_s/f_t`，价格为 `P_s·f_t/f_s`，仅在 `seg_s = seg_ref_t` 时使用。使用 `f_t` SHALL 在字典中登记为未验证的可得性假设 A1，并输出 `split_on_t`。`f_t` 缺失或 ≤0 时 SHALL 沿用 t 之前最后已知因子并标 `factor_carried`；无任何已知因子时需换算的特征 SHALL 为 NaN 并标 `factor_unknown`。历史槽位 `f_s` 缺失或 ≤0 时该槽位 MUST NOT 用于换算族。t 日 `split_status` 为 `split_unresolved` 或 `factor_missing` 时 SHALL 只输出 `expost_split_issue_t`，MUST NOT 影响特征或样本。

#### Scenario: Two-for-one split on day t
- **WHEN** t 日 `ajexdi` 由 2 变为 1 且 t−1 之前竞价量为 1000 股
- **THEN** t 日特征按 2000 股计算，`split_on_t` 为真

#### Scenario: Unresolved split on day t is diagnostic only
- **WHEN** t 日 `split_status='split_unresolved'`
- **THEN** t 日特征与该状态为 ok 时相同，`expost_split_issue_t` 为真

#### Scenario: Break before t cuts history
- **WHEN** t−10 为 `split_unresolved`
- **THEN** t 日窗口只使用 t−10 及以后的观测

#### Scenario: Missing factor on t
- **WHEN** t 日 `ajexdi` 缺失而 t−1 为 1.0
- **THEN** 以 1.0 换算，`factor_carried` 为真

### Requirement: Market cap from same-day price and shares
`logmcap_lag1` SHALL 为 t−1 当日 `log(PX_LAST·shares_outstanding)`（ADR 已按 `adrrc` 换算），任一缺失或 ≤0 时为 NaN，MUST NOT 向前沿用更早的股本或价格。报告 SHALL 列出 `split_on_t` 附近市值跳变（|Δlogmcap| > ln 1.8）的行数。

#### Scenario: Missing shares at t−1
- **WHEN** t−1 的 `shares_outstanding` 缺失而 t−2 有值
- **THEN** `logmcap_lag1` 为 NaN

### Requirement: Strict lag and last-valid naming
`*_lag1` SHALL 只取 t−1 会话，t−1 不满足族条件时为 NaN。最近有效观测 SHALL 命名为 `*_last`，取 W(t,5) 内最近可用观测，并输出 `*_last_age`（1–5）。系统 MUST NOT 以 `lag1` 命名最近有效值。

#### Scenario: Day after event
- **WHEN** t−1 为事件日、t−2 有效
- **THEN** `a_lag1` 为 NaN，`a_last` 等于 t−2 的换算值，`a_last_age = 2`

### Requirement: Daily core features
系统 SHALL 输出日频核心特征 `a_last`、`a_last_age`、`a_ma5`（5/3）、`a_ma22`（22/15）、`d_last`、`d_last_age`、`d_ma22`（22/15）、`logmcap_lag1`、`dow`、`month_end`、`quarter_end`、`opex`，诊断列 `a_lag1`、`d_lag1`、`a_n22`、`d_n22`。竞价均值在 log1p 空间、成交量均值在 log 空间计算。日历特征 SHALL 只由 XNYS 日历决定。

#### Scenario: Minimum periods
- **WHEN** W(t,22) 内只有 14 个 U_A 观测
- **THEN** `a_ma22` 为 NaN；有 15 个时有值

#### Scenario: Same-day independence
- **WHEN** 只修改 t 日的标签、成交量、价格与股本
- **THEN** t 日全部特征不变，`y` 改变

### Requirement: Target and selection metadata
daily 表 SHALL 包含目标 `y = log1p(label_t)`（仅 `valid_day_t`）与 `label`，以及逐值等于 processed 的元数据 `label_status`、`valid_day`、`mask_reason`、`pit_eligible`、`research_status`、`research_included_pit`、`research_included_expost`、`split_status`；仅供报告的事后字段 `security_status`、`exclusion_reason`；派生标志 `split_on_t`、`factor_carried`、`factor_unknown`、`expost_split_issue_t`、`interval_id`、`interval_start` 与 `core_required_complete`（`a_last`、`a_ma5`、`a_ma22`、`d_last`、`d_ma22`、`logmcap_lag1` 全部非缺失）。字典 SHALL 把事后字段标注为 ex-post 且不得作为特征。特征层 MUST NOT 依据任何元数据删除行。

#### Scenario: Metadata equals processed
- **WHEN** 按主键比较 daily 表与 `stock_day_quality` 的上述透传列
- **THEN** 全部逐值相等（含缺失）

#### Scenario: Sample from features alone
- **WHEN** 模型层只读取 daily 表
- **THEN** 能以 `research_included_pit ∧ core_required_complete` 构造评估样本

### Requirement: Intraday core features
系统 SHALL 输出 证券 × 日 × 检查点 的 `C_b`（t 日截至 b 的连续成交量，原生股数）、`mu_b`（W(t,20) 内 U_C 观测换算后 log1p 均值，最少 12）与 `q_b = log1p(C_b) − mu_b`，行集合等于 `checkpoints_research` 主键集合。`not_applicable` 或 `volume_valid` 为假的检查点 `C_b` MUST 为 NaN。

#### Scenario: Split during checkpoint history
- **WHEN** t−5 发生一致的 2:1 拆股
- **THEN** t−20…t−6 的检查点量乘 2 后参与 `mu_b`

### Requirement: Monthly behaviour profiles
系统 SHALL 在每月第一个会话 m 为该月有面板行的每只证券计算画像，窗口为 m 之前 126 个会话，只用 U_A 且与 m 处于同一连续市场区间的观测：`pf_lshare = mean ln(V^A_s/PX_VOLUME_s)`（≥60 个观测）；`e_s = ln(1+V^A_s) − a_ma22_s`（仅 `a_ma22_s` 非缺失），`med = median(e)`，`pf_dev_std = 1.4826·median|e_s − med|`（≥60 个 e）；`ẽ_s = clip(e_s, med ± 4·pf_dev_std)`；`pf_dev_ac1` 为 `ẽ_s` 与 `ẽ_{prev(s)}` 的 Pearson 相关（ddof=0），`prev(s)` 为紧邻的上一个会话，两者都须在 e 集合中（≥50 对）；`pf_dev_std ≤ 1e-6` 或任一配对向量方差为 0 时为退化。默认三项任一不满足时三项同时为 NaN，并记录 `profile_status`（`not_listed`、`interval_break`、`insufficient_history`、`insufficient_pairs`、`degenerate_dispersion`、`ok`）。profiles 表 SHALL 输出画像所用区间的 `interval_id` 与 `market`。可选 `pf_dollar_auc = mean ln(V^A_s·PX_LAST_s)` SHALL 计算但标记为 `profile_ext`。画像 MUST NOT 做标准化、winsorize 或聚类。

#### Scenario: Pair across masked day
- **WHEN** s−1 为屏蔽日
- **THEN** `(e_s, e_{s−1})` 不计入配对

#### Scenario: New listing
- **WHEN** 证券在 m 之前只有 50 个可用 e
- **THEN** 该月三项画像为 NaN，`profile_status='insufficient_history'`

#### Scenario: Consistent split inside the window
- **WHEN** 合成数据在窗口内发生因子与价格一致的 2:1 拆股，且拆股前后按新股数口径的真实量不变
- **THEN** `pf_lshare`、`pf_dollar_auc` 与不拆股版本逐值相同（同日比值/乘积）；`pf_dev_std`、`pf_dev_ac1` 只近似相同，差异来自 `e_s` 中 log1p 的 +1 项，量级约 `1/V^A`（测试容差按窗口内最小竞价量设定）

#### Scenario: Future data does not change profiles
- **WHEN** 扰动 m 及以后的全部数据
- **THEN** 月份 m 及以前的画像逐位不变

### Requirement: Feature dictionary
`feature_dictionary.csv` SHALL 为每个输出列登记 name、table、stage（key/target/flag/daily/intraday/profile）、group（core、diagnostic、profile、profile_ext、metadata、ex_post 等）、定义、来源字段、窗口与最少观测、可用条件、可得时点、是否依赖假设 A1。各表输出列与字典 MUST 一一对应。

#### Scenario: Undocumented column
- **WHEN** 输出含字典未登记的列
- **THEN** 构建失败

### Requirement: Immutable versioned feature output
版本 SHALL 先完整写入临时目录，校验后改名为 `Data/features/.versions/<id>/`，再原子切换 `Data/features/current`。manifest SHALL 记录 FEATURE_VERSION、processed 目录/版本/指纹与输入摘要、config、代码摘要、git 提交、各文件行数与排序后内容哈希。指纹相同时 SHALL 复用现有版本且不写任何文件。已发布版本目录 MUST NOT 被任何后续命令写入：`verify` 只读；`report` SHALL 写入 `Data/features/reports/<id>/`。同一输入两次构建的排序后内容哈希 MUST 相同。

#### Scenario: Reuse
- **WHEN** processed 指纹、输入摘要、配置与代码未变时再次 build
- **THEN** 返回现有版本，版本目录内文件与修改时间不变

#### Scenario: Report does not touch the version
- **WHEN** 对已发布版本运行 `python -m auction_features report`
- **THEN** 版本目录内所有文件的 sha256 不变，新报告写入 `Data/features/reports/<id>/`

#### Scenario: Failed build
- **WHEN** 构建中途失败
- **THEN** `current` 仍指向原版本，临时目录保留供诊断

### Requirement: Feature quality report and acceptance comparisons
报告 SHALL 包含：按特征、年份、市场的缺失率（全面板与 pit 行分列）；窗口内可用观测数分布；分位数与异常值计数；每日 pit 行与 `core_required_complete` 覆盖；2022-04 后 pit 行核心完整率及不完整原因分解；拆股与因子诊断计数；市值跳变计数；每月每市场画像缺失比例及原因；画像普通/稳健版本对比与相邻非重叠窗口重测相关。核心完整率 MUST NOT 作为构建失败条件；低于 99.5% 时 SHALL 在报告中标为复核项。不变性测试（截断、未来扰动、个股隔离、同日独立、研究标志无关）SHALL 要求逐位相等且 NaN 位置相同；与逐行参考实现的对拍 SHALL 要求 NaN 位置相同、整数/布尔/age/计数列逐值相等、浮点列 `|Δ| ≤ 1e-12 + 1e-12·|ref|`。

#### Scenario: Completeness below review trigger
- **WHEN** 2022-04 后 pit 行核心完整率为 99.3%
- **THEN** 构建成功，报告把该项标为复核并给出原因分解

#### Scenario: Reference comparison with rounding
- **WHEN** 向量化 `a_ma22` 与参考实现相差 3e-16
- **THEN** 对拍通过

