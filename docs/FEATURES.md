# 特征面板（features-v1）

由 `python -m auction_features build` 生成，规格见 [openspec/specs/feature-panel](../openspec/specs/feature-panel/spec.md)（设计：[archive/2026-11-23-feature-panel](../openspec/changes/archive/2026-11-23-feature-panel/design.md)）。本文件的定义表由 `auction_features/dictionary.py` 生成（`feature_dictionary.csv` 同源），修改字典后重新生成。

## 使用要点

- **输入固定**：构建开始时把 `Data/processed/wrds_current` 解析为实际版本目录，并按 processed manifest 校验三个输入文件的 sha256；结束时复核。特征 manifest 记录所用目录、版本与指纹。
- **输出**：`Data/features/current → .versions/<id>/`，含 `daily/<MARKET>/<YEAR>.parquet`、`intraday/<MARKET>/<YEAR>.parquet`、`profiles/profiles.parquet`、`feature_dictionary.csv`、`manifest.json`、`report/`。读取用 `auction_features.build.load_table(version_dir, name)` 或 `pd.read_parquet(dir)`。已发布版本不再被改写；`report` 命令写到 `Data/features/reports/<id>/`。
- **行范围**：daily 行集合 = `stock_day_quality`（全面板，不按研究标志筛选）；模型层用 `research_included_pit & core_required_complete` 构造评估样本。`security_status`、`exclusion_reason` 是事后字段，只用于报告。
- **时点**：所有 daily 特征只用 t 之前的观测、t 日日历与 `f_t`（假设 A1：拆股生效与比例开盘前已公告，未经数据验证；`split_on_t` 供稳健性剔除）。竞价、成交量、检查点族历史不跨连续市场区间（转板、回转、重新上市）与拆股分段（以 t−1 及以前最后已知分段为准）；屏蔽日占位；价格族可用屏蔽日。
- **命名**：`*_lag1` 为严格 t−1；`*_last`、`*_last_age` 为 5 个会话内最近可用观测及其间隔。
- **拆股一致性**：同日比值/乘积（`pf_lshare`、`pf_dollar_auc`）在一致拆股下精确不变；跨日量（`a_*`、`d_*`、`mu_b`）经 `f` 换算在同一分段内一致；`pf_dev_std`、`pf_dev_ac1` 中 `e_s` 以 log1p 计算，一致拆股前后只近似不变（偏差约 `1/V^A`）；市值依赖价格与股本同日更新。
- **不做拟合**：标准化、winsorize、填补、聚类都在模型层，只用对应训练窗口。

## 字段定义

| 字段 | 表 | 阶段 | 组 | 定义 | 来源 | 窗口/最少 | 可用条件 | 可得时点 | A1 |
|---|---|---|---|---|---|---|---|---|---|
| security_id | daily\|intraday\|profiles | key | key | GVKEY:IID | stock_day_quality |  |  | metadata | False |
| date | daily\|intraday | key | key | XNYS session | stock_day_quality |  |  | metadata | False |
| checkpoint | intraday | key | key | New York time checkpoint | checkpoints_research |  |  | metadata | False |
| month | profiles | key | key | YYYY-MM of the profile | XNYS |  |  | metadata | False |
| m_date | profiles | key | metadata | first XNYS session of the month | XNYS |  |  | known in advance | False |
| market | daily\|intraday\|profiles | key | metadata | historical listing market of the row (profiles: of m) | sec_idhist EXCHG |  |  | before open of t | False |
| ticker | daily | key | metadata | historical ticker | sec_idhist TIC |  |  | metadata | False |
| interval_id | daily\|profiles | flag | metadata | continuous market interval number within the security (1-based; new interval after an unlisted session or a market change); profiles: interval of m, 0 if not listed | sec_idhist |  |  | before open of t | False |
| interval_start | daily | flag | metadata | t is the first session of its continuous market interval | sec_idhist |  |  | before open of t | False |
| y | daily | target | target | log1p(label_t) if valid_day_t else NaN | label |  |  | t post-close | False |
| label | daily | target | target | listing-venue closing cross shares (native, passthrough) | stock_day_quality |  |  | t post-close | False |
| label_status | daily | flag | metadata | passthrough | stock_day_quality |  |  | t post-close | False |
| valid_day | daily | flag | metadata | passthrough | stock_day_quality |  |  | t post-close | False |
| mask_reason | daily | flag | metadata | passthrough: event_day / half_day / empty | stock_day_quality |  |  | known in advance | False |
| pit_eligible | daily | flag | metadata | passthrough (strict PIT, needs valid_day_t) | stock_day_quality |  |  | t post-close | False |
| research_status | daily | flag | metadata | passthrough (deferral is a full-window decision) | stock_day_quality |  |  | ex-post | False |
| research_included_pit | daily | flag | metadata | passthrough; study sample filter | stock_day_quality |  |  | t post-close | False |
| research_included_expost | daily | flag | metadata | passthrough | stock_day_quality |  |  | ex-post | False |
| split_status | daily | flag | metadata | passthrough processed split status of t | stock_day_quality |  |  | t post-close | False |
| security_status | daily | flag | ex_post | passthrough ex-post 5% status; report only, never a feature | stock_day_quality |  |  | ex-post | False |
| exclusion_reason | daily | flag | ex_post | passthrough funnel reason; report only, never a feature | stock_day_quality |  |  | ex-post | False |
| split_on_t | daily | flag | flag | f_t differs from the last known factor before t | ajexdi |  |  | before open of t | True |
| factor_carried | daily | flag | flag | f_t missing or <=0; last known factor before t used | ajexdi |  |  | before open of t | False |
| factor_unknown | daily | flag | flag | no known factor at or before t; converted features NaN | ajexdi |  |  | before open of t | False |
| expost_split_issue_t | daily | flag | ex_post | split_status_t in {split_unresolved, factor_missing}; needs t close; never used for features or samples | split_status |  |  | t post-close | False |
| core_required_complete | daily | flag | flag | a_last, a_ma5, a_ma22, d_last, d_ma22, logmcap_lag1 all non-missing | derived |  |  | before open of t | False |
| a_last | daily | daily | core | most recent usable log1p(V^A_s f_s/f_t) in the 5 prior sessions; converted with f_s/f_t | label, ajexdi | 5/1 | U_A valid_day; same continuous market interval; seg_s = seg_ref_t | before open of t | True |
| a_last_age | daily | daily | core | sessions between t and the a_last observation (1-5) | derived | 5/1 | U_A valid_day; same continuous market interval; seg_s = seg_ref_t | before open of t | False |
| a_ma5 | daily | daily | core | mean log1p(V^A_s f_s/f_t) over usable slots | label, ajexdi | 5/3 | U_A valid_day; same continuous market interval; seg_s = seg_ref_t | before open of t | True |
| a_ma22 | daily | daily | core | mean log1p(V^A_s f_s/f_t) over usable slots | label, ajexdi | 22/15 | U_A valid_day; same continuous market interval; seg_s = seg_ref_t | before open of t | True |
| d_last | daily | daily | core | most recent usable log(PX_VOLUME_s f_s/f_t) in the 5 prior sessions | cshtrd, ajexdi | 5/1 | U_V listed, unmasked, PX_VOLUME>0; same continuous market interval; seg_s = seg_ref_t | before open of t | True |
| d_last_age | daily | daily | core | sessions between t and the d_last observation (1-5) | derived | 5/1 | U_V listed, unmasked, PX_VOLUME>0; same continuous market interval; seg_s = seg_ref_t | before open of t | False |
| d_ma22 | daily | daily | core | mean log(PX_VOLUME_s f_s/f_t) over usable slots | cshtrd, ajexdi | 22/15 | U_V listed, unmasked, PX_VOLUME>0; same continuous market interval; seg_s = seg_ref_t | before open of t | True |
| logmcap_lag1 | daily | daily | core | strict t-1 log(PX_LAST * shares_outstanding) | prccd, cshoc, adrrc | 1/1 | U_P PX_LAST>0 (masked allowed) and shares_outstanding>0 on t-1; no carry-forward | before open of t | False |
| dow | daily | daily | core | day of week 0=Mon (categorical) | XNYS |  |  | known in advance | False |
| month_end | daily | daily | core | last XNYS session of the month | XNYS |  |  | known in advance | False |
| quarter_end | daily | daily | core | last XNYS session of Mar/Jun/Sep/Dec | XNYS |  |  | known in advance | False |
| opex | daily | daily | core | third Friday of the month, or the preceding session if closed | XNYS |  |  | known in advance | False |
| a_lag1 | daily | daily | diagnostic | strict t-1 auction value; NaN if t-1 unusable | label, ajexdi | 1/1 | U_A valid_day; same continuous market interval; seg_s = seg_ref_t | before open of t | True |
| d_lag1 | daily | daily | diagnostic | strict t-1 volume value; NaN if t-1 unusable | cshtrd, ajexdi | 1/1 | U_V listed, unmasked, PX_VOLUME>0; same continuous market interval; seg_s = seg_ref_t | before open of t | True |
| a_n22 | daily | daily | diagnostic | usable auction slots in the 22 prior sessions | derived |  | U_A valid_day; same continuous market interval; seg_s = seg_ref_t | before open of t | False |
| d_n22 | daily | daily | diagnostic | usable volume slots in the 22 prior sessions | derived |  | U_V listed, unmasked, PX_VOLUME>0; same continuous market interval; seg_s = seg_ref_t | before open of t | False |
| C_b | intraday | intraday | intraday_core | continuous volume 09:30 to checkpoint b on t (native shares); NaN unless applicable and volume_valid | TAQ cumvol |  | applicable, volume_valid | checkpoint b on t | False |
| mu_b | intraday | intraday | intraday_core | mean log1p(C_{s,b} f_s/f_t) over usable slots | TAQ cumvol, ajexdi | 20/12 | U_C applicable, volume_valid, unmasked; same continuous market interval; seg_s = seg_ref_t | before open of t | True |
| q_b | intraday | intraday | intraday_core | log1p(C_b) - mu_b | derived |  |  | checkpoint b on t | True |
| pf_lshare | profiles | profile | profile | mean ln(label_s/PX_VOLUME_s) | label, cshtrd | 126/60 | U_A in the continuous market interval of m; 126 sessions before m | before open of m | False |
| pf_dev_std | profiles | profile | profile | 1.4826*median\|e-median(e)\|, e = ln(1+label_s) - a_ma22_s | derived | 126/60 | U_A in the continuous market interval of m; 126 sessions before m | before open of m | False |
| pf_dev_ac1 | profiles | profile | profile | Pearson (ddof=0) of clipped e on adjacent-session pairs; clip at median +/- 4*pf_dev_std | derived | 126/50 pairs | U_A in the continuous market interval of m; 126 sessions before m | before open of m | False |
| pf_dollar_auc | profiles | profile | profile_ext | mean ln(label_s * PX_LAST_s); optional size extension | label, prccd | 126/60 | U_A in the continuous market interval of m; 126 sessions before m | before open of m | False |
| pf_dev_std_plain | profiles | profile | diagnostic | std(e, ddof=1) | derived |  | U_A in the continuous market interval of m; 126 sessions before m | before open of m | False |
| pf_dev_ac1_plain | profiles | profile | diagnostic | Pearson of unclipped e on the same pairs | derived |  | U_A in the continuous market interval of m; 126 sessions before m | before open of m | False |
| pf_dev_ac1_spearman | profiles | profile | diagnostic | Spearman of e on the same pairs | derived |  | U_A in the continuous market interval of m; 126 sessions before m | before open of m | False |
| n_A | profiles | profile | diagnostic | usable observations in the window and interval | derived |  | U_A in the continuous market interval of m; 126 sessions before m | before open of m | False |
| n_e | profiles | profile | diagnostic | observations with e | derived |  | U_A in the continuous market interval of m; 126 sessions before m | before open of m | False |
| n_p | profiles | profile | diagnostic | adjacent-session e pairs | derived |  | U_A in the continuous market interval of m; 126 sessions before m | before open of m | False |
| profile_status | profiles | profile | flag | ok / not_listed / interval_break / insufficient_history / insufficient_pairs / degenerate_dispersion | derived |  |  | before open of m | False |
