# 待核实与待改进事项

基线：处理版本 `wrds-process-v3`，已归档变更见 [openspec/changes/archive](../openspec/changes/archive/)。每项关闭时注明所在 OpenSpec change 与提交。研究口径见 [数据流程说明](WRDS_DATA_PIPELINE.md)。

## 事件日期（`auction_data/reference/index_events.csv` 中 `verified=false`）

| 执行收盘日 | 事件 | 当前处理 | 待做 | 状态 |
|---|---|---|---|---|
| 2025-06-20 | S&P 季度再平衡 | 按美国方法论第三个周五规则排除 | 查找美国指数官方生效通知（季度股数/IWF 调整或成分变更公告）。已知：S&P 500 当季无成分变化；仅找到 S&P/NZX 官方通知（生效 2025-06-23 开盘） | 未确认 |
| 2026-12-18 | S&P 季度再平衡 | 按规则登记；晚于当前数据截止日，尚不影响产物 | 12 月初 S&P 公告后核对执行收盘日与生效日 | 未来事件 |
| 2026-12-18 | Nasdaq-100 年度重构 | 同上 | 12 月中 Nasdaq 公告后核对 | 未来事件 |

不因"无成分股变化"取消排除；任何日期变更需单独讨论后另开 change。

## 产物与报告

- `report/funnel.csv` 的 `with_listing_boundary` 证券数（6,997）不含 19 只 `ticker_history_gap` 暂缓证券，而同行股票日包含它们；下次规则版本升级时让证券数与股票日口径一致（数据本身无误）。
- `report/daily_counts.png` 的 PIT 曲线目前统计严格 `pit_eligible`，研究训练使用 `research_included_pit`（两者仅差 TORO 654 行，单日最多 1 只）。下次规则版本升级时改为同时显示或改用 `research_included_pit`。

## 数据与口径后续项

- 下载 Compustat company 表（SIC/GICS）以标记 REIT（6798）与 SPAC（6770）；当前 `is_reit`、`is_spac` 为 NA。
- WRDS 一次性核验：竞价打印时间戳早于收盘时是否被 `continuous_volume` 重复计入。
- WRDS 一次性核验：Compustat EXCHG `24` 及其 TAQ 场所代码（2026-10 起 ET 等迁出证券）。
- 124 只暂缓证券（105 `unknown_security_type`、19 `ticker_history_gap`）随输入更新自动重新评估；无需手工操作，关注 `report/security_status_summary.csv`。
- 2,257 只证券因全历史异常率 >5% 被事后剔除，约 8.4% 应有股票日缺上市场所竞价标签；按市场与规模的分解尚未做。

## 特征面板（见 [FEATURES.md](FEATURES.md)；当前 features-v2）

- **假设 A1 未验证**：t 日拆股生效与比例开盘前已公告，特征按 `f_t` 换算。pit 行中 855 行 `split_on_t`；模型稳健性检验剔除这些行。
- **股本更新滞后**：拆股日 `cshoc` 约 9% 未同日更新，`logmcap_lag1` 会跳变，不修正；全面板 |Δlogmcap| > ln 1.8 共 6,691 次，其中 176 次在拆股后两天内。
- **画像口径**：features-v2 起 `e_s = ln(V^A_s) − la_ma22_s`，一致拆股下画像在浮点舍入内不变（v1 的 log1p 口径偏差约 `1/V^A`，已由 `profile-log-deviation` 变更修正）。
- **画像诊断（训练期，月初 < 2025-01-01）**：6 个月重测相关 NYSE `pf_lshare` 0.93、`pf_dev_std` 0.72、`pf_dev_ac1` 0.18；Nasdaq 0.95、0.87、0.17。`pf_dev_ac1` 自相关长期稳定性较低，只作诊断。Nasdaq 上 `pf_lshare` 与 `pf_dev_std` 相关 −0.84，与 `logmcap` 相关 0.77、−0.72（NYSE 为 0.37、−0.36），需在聚类结果中报告规模主导诊断、簇规模与稳定性；画像是否提供额外预测价值由 D1/D2/D3 回答。
- 画像 2022-04 全部缺失；训练期缺失率 NYSE 19.4%、Nasdaq 27.3%（样本起点后历史不足与新股），保留为 `unassigned` 并在模型报告中披露。
