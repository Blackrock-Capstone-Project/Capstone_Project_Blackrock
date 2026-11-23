## MODIFIED Requirements

### Requirement: Monthly behaviour profiles
系统 SHALL 在每月第一个会话 m 为该月有面板行的每只证券计算画像，窗口为 m 之前 126 个会话，只用 U_A 且与 m 处于同一连续市场区间的观测：`pf_lshare = mean ln(V^A_s/PX_VOLUME_s)`（≥60 个观测）；`e_s = ln(V^A_s) − la_ma22_s`，其中 `la_ma22_s` 为与 `a_ma22_s` 相同可用槽位集合（W(s,22) 内 U_A、同一连续市场区间、`seg_ref_s`、最少 15）上 `ln(V^A_r·f_r/f_s)` 的均值（仅 `la_ma22_s` 非缺失），`med = median(e)`，`pf_dev_std = 1.4826·median|e_s − med|`（≥60 个 e）；`ẽ_s = clip(e_s, med ± 4·pf_dev_std)`；`pf_dev_ac1` 为 `ẽ_s` 与 `ẽ_{prev(s)}` 的 Pearson 相关（ddof=0），`prev(s)` 为紧邻的上一个会话，两者都须在 e 集合中（≥50 对）；`pf_dev_std ≤ 1e-6` 或任一配对向量方差为 0 时为退化。`la_ma22_s` 只在画像内部使用，MUST NOT 改变日频目标 `y = log1p(V^A)` 或 `a_*` 特征的定义。默认三项任一不满足时三项同时为 NaN，并记录 `profile_status`（`not_listed`、`interval_break`、`insufficient_history`、`insufficient_pairs`、`degenerate_dispersion`、`ok`）。profiles 表 SHALL 输出画像所用区间的 `interval_id` 与 `market`。可选 `pf_dollar_auc = mean ln(V^A_s·PX_LAST_s)` SHALL 计算但标记为 `profile_ext`。画像 MUST NOT 做标准化、winsorize 或聚类。

#### Scenario: Pair across masked day
- **WHEN** s−1 为屏蔽日
- **THEN** `(e_s, e_{s−1})` 不计入配对

#### Scenario: New listing
- **WHEN** 证券在 m 之前只有 50 个可用 e
- **THEN** 该月三项画像为 NaN，`profile_status='insufficient_history'`

#### Scenario: Consistent split inside the window
- **WHEN** 合成数据在窗口内发生因子与价格一致的 2:1 拆股，且拆股前后按新股数口径的真实量不变
- **THEN** `pf_lshare`、`pf_dollar_auc`、`pf_dev_std`、`pf_dev_ac1` 与不拆股版本在浮点舍入内相同（`|Δ| ≤ 1e-12 + 1e-12·|x|`）

#### Scenario: Daily definitions unchanged
- **WHEN** 以 features-v2 代码与 features-v1 代码在同一输入上构建
- **THEN** daily 与 intraday 表的内容哈希相同，只有 profiles 中依赖 `e_s` 的列不同

#### Scenario: Future data does not change profiles
- **WHEN** 扰动 m 及以后的全部数据
- **THEN** 月份 m 及以前的画像逐位不变
