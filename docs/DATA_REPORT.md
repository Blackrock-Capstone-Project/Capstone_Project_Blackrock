# 美股收盘竞价数据报告

本报告依据当前代码（`auction_data/`）与已验收的实际产物编写，供研究团队了解数据从哪里来、怎样处理、得到什么、还有哪些限制。运行命令与输出契约见 [数据流程说明](WRDS_DATA_PIPELINE.md)，从干净 checkout 复现与差异核查见 [复现与差异核查](REPRODUCIBILITY.md)，未决事项见 [待核实事项](OPEN_ITEMS.md)，正式要求见 [规格](../openspec/specs/)。

**标注约定**：**【事实】** 由数据、官方来源或测试直接确认；**【选择】** 是研究口径决定，可以改变但需另开变更；**【待核实】** 尚未确认，列入 OPEN_ITEMS。

## 0. 版本与范围

| 项 | 值 |
|---|---|
| 数据区间 | 2022-01-03 至 2026-11-19（两来源共同已发布截止日；2026-11-20 发布滞后，未纳入） |
| 市场 | NYSE（Compustat EXCHG 11）、Nasdaq（EXCHG 14） |
| raw 版本 | `RAW_VERSION = wrds-v1`；1,286 个分区（日频 59 个月、TAQ 1,226 个交易日、历史标识 1 个） |
| 处理版本 | `PROCESS_VERSION = wrds-process-v3`；`Data/processed/.wrds_versions/4eb19f3a851541f5ba1d14a1dc7c445c`；处理指纹 `cc72b366…7fc6` |
| 代码基线 | 提交 `b50515d`（tag `baseline-wrds-process-v3`）；结构调整后同一输入的产物逐字节一致（见 §13） |
| 研究训练筛选 | `research_sample.csv.gz` 中 `research_included_pit` 为真的行【选择】 |

## 1. 数据源与权限【事实】

| 来源 | WRDS 表 | 用途 |
|---|---|---|
| Compustat North America Daily | `comp_na_daily_all.secd` | 日频价格、成交量、股本、调整因子、证券类型 |
| Compustat 历史标识 | `comp_na_daily_all.sec_idhist` | `EXCHG`（上市交易所）与 `TIC`（ticker）的生效区间 |
| TAQ Millisecond | `taqm_YYYY.ctm_YYYYMMDD` | 逐笔成交，在 WRDS 服务器端汇总成每只股票每天一行 |

- 需要账号对上述表有 SELECT 权限；`python -m auction_data check` 先做只读探测，权限不足时报告 SQLSTATE（如 42501），不替换为 CRSP 或 Bloomberg。
- 账号与密码在运行时输入，需要时完成 Duo 批准；源码、日志、元数据与报告不记录账号、密码或连接串。
- 不使用 Bloomberg 或其他来源；也不使用当前时点股票池过滤历史。

## 2. 下载范围与字段

### 2.1 日频（Compustat）【事实】

- 按月分区 `Data/raw/wrds/daily/months/YYYYMM.csv.gz`，共 10,113,152 行、13,043 个证券（GVKEY:IID）；只取当日 `curcdd='USD'` 且当日历史 `EXCHG ∈ {11,14}` 的证券日。
- 字段：`gvkey, iid, datadate, tic, cusip, curcdd, prccd, prcod, prchd, prcld, cshtrd, cshoc, ajexdi, prcstd, tpci, adrrc`，以及当日的 `historical_exchg`、`historical_tic`。
- 处理后：`PX_LAST`（收盘）、`PX_VOLUME`（`cshtrd`，Compustat 报告的成交股数）、`shares_outstanding`（普通证券取 `cshoc`；ADR 取 `cshoc/adrrc`）、`CUR_MKT_CAP`、`adjustment_factor`（`ajexdi`）。`prcstd=4` 时 high/low 实为收盘买卖价，移到 `closing_bid/ask`。
- 历史标识表 31,797 行，每次 `update` 整表重取。

### 2.2 TAQ 日内汇总【事实】

- 按交易日分区 `Data/raw/taq/days/YYYYMMDD.csv.gz`，共 13,942,671 个"符号 × 交易日"，覆盖当天所有交易过的符号（包括 ETF、权证、优先股等；股票池在处理阶段筛选）。
- 服务器端汇总口径：只用 `tr_corr='00'`（未更正成交）；连续成交从 09:30 到收盘时间；收盘时间按 XNYS 日历（半日市 13:00）。

| 字段 | 定义 |
|---|---|
| `cumvol_10:00 … cumvol_15:50` | 09:30 至各检查点（纽约时间）的累计成交股数；半日市只有收盘前的检查点 |
| `continuous_volume` | 09:30 至收盘前（`time_m < close`）的成交股数 |
| `xvol_n/q/p/a/z` | 收盘前 1 分钟至收盘后 5 分钟内、条件码含 `6` 的收盘竞价成交，按场所 NYSE/Nasdaq/Arca/American/Cboe BZX 分列 |
| `auction_volume_all` | 上述窗口内所有场所的竞价成交合计 |
| `auction_volume`、`listing_ex` | 竞价量最大的场所及其量（推断，非官方上市身份，仅供对账） |
| `auction_price`、`n_prints` | 竞价成交价（取 MAX）与笔数，跨场所聚合 |
| `trf_close_volume` | 收盘后 30 分钟内 `ex='D'`（TRF）、价格等于 `auction_price` 的成交 |

- TAQ 成交量含零股成交，约 99% 的 `continuous_volume` 为小数；竞价量为整数。
- 日内数据是服务器端汇总的检查点与竞价量，**不是完整逐笔存档**【事实】；没有价格路径、VWAP 或交易所收盘不平衡（imbalance）数据。

### 2.3 下载与更新机制【事实】

- 只下载已收盘并经过 30 分钟的交易日；分别探测两来源的发布日期，取共同截止日处理。
- 每个分区旁的 `.json` 记录请求、行数、列、SHA256 与下载时间；`pipeline_plan.json` 列出全部预期分区，既查整块遗漏也查块内缺日。
- 每次 `update` 默认重取最近 5 个已发布交易日涉及的分区，以吸收 WRDS 修订；更早的修订需 `--refresh-start`。
- 本次 1,286 个分区全部通过离线审计，无缺日。

## 3. 历史身份匹配

- **证券 ID**【事实】：`security_id = GVKEY:IID`，跨日稳定。
- **上市区间**【事实】：只用 `sec_idhist` 的 `EXCHG` 生效区间决定证券在 NYSE/Nasdaq 的应有交易日，不用首末成交日代替。NYSE↔Nasdaq 转板时证券历史不中断（如 OBK 2023-05-22 由 14 变为 11）；转到其他交易所代码时区间结束。
- **Compustat↔TAQ 符号链接**【选择】：用当日有效 `TIC` 映射 TAQ 符号，`.` 换为 `/`（`BRK.B` ↔ `BRK/B`），优先股后缀单独处理；同日一对多或多对一即为歧义，不强行连接。这是**可审计的历史 ticker 匹配，不是官方永久链接**。
- **身份状态**：`unique`（唯一匹配）/ `unmatched`（当日无 TAQ 记录）/ `ambiguous` / `missing_identity`（上市区间内无有效 TIC）。
- 在已确认普通股的上市区间内，6,314,107 个股票日身份可用，2,786 个为 `missing_identity`（集中在 19 只证券，见 §9）。

## 4. 收盘竞价标签

- **定义**【选择】：标签是**当日历史上市交易所实际执行的收盘竞价股数**：EXCHG 11 取 `xvol_n`，EXCHG 14 取 `xvol_q`。对应场所为空即标签缺失，**不回退**到其他场所或最大场所。模型目标为 `log1p(标签)`，评价时还原为股数。
- **为何不用最大场所**【事实】：TAQ 的 `listing_ex` 是由竞价量最大场所推断的，不是官方上市身份；部分证券在其他场所也有小额 `6` 类成交。
- **对账**（排除屏蔽日后的应有股票日 6,049,820 个）【事实】：

| 类别 | 股票日 | 含义 |
|---|---|---|
| consistent | 5,535,754 | 上市场所有竞价，且是最大场所 |
| listing_cross_missing | 510,942 | 上市场所无竞价（其中 360,061 有 TAQ 记录但该场所无 `6` 成交，153,435 当日无 TAQ 记录） |
| venue_conflict | 455 | 其他场所竞价更大或仅其他场所有竞价 |
| identity_unresolved | 2,669 | 身份歧义或缺 ticker |

  按市场：Nasdaq 应有股票日中标签可用 89.7%，NYSE 94.9%。
- **迁出已知场所的竞价**【事实】：2026-10 起 ET、SUN、SUNC、USAC、DDS、OBK、CECO、IBOC、TXCP、CFR 的 Compustat EXCHG 变为 `24`，同日 TAQ 竞价移出 N/Q/P/A/Z。它们在变更日退出上市区间，不计异常。全部"竞价在五个场所之外"的符号（704 个、3,458 个符号日，多为优先股、测试符号与上述证券）列于 `report/outside_venue_crosses.csv`。EXCHG `24` 对应的交易所与 TAQ 场所代码【待核实】。

## 5. 股票池与样本筛选

1. **普通股**【选择】：只保留 Compustat `tpci='0'`。13,147 个证券中 6,026 个为非普通股：ETF `%` 1,631、权证/权利 `2` 1,336、units `4` 1,130、优先股 `1` 662、ADR `F` 535、封闭式基金 `7` 423、债券型证券 `R` 269（不是 REIT）、存托优先股 `G` 26、ADR `S` 14。`tpci` 为当前快照，不是逐日历史值【事实】。REIT 与 SPAC 随普通股纳入，`is_reit`、`is_spac` 为 NA（无行业字段）【选择】。
2. **有效股票日**【选择】：日频成交量与标签都有限且 >0、身份唯一、非屏蔽日。
3. **事后整股筛选**【选择】：全历史异常率 = 异常日 / 应有交易日（请求范围 ∩ N/Q 上市区间 ∩ 共同已验证覆盖 − 屏蔽日）；严格 >5% 整只剔除，恰好 5% 保留。完整分区内的个股缺记录计异常；来源级缺分区不计（本次没有）。
4. **逐日筛选**【选择】：t 日之前 10 个 XNYS 会话中至少 6 个有效日，屏蔽日占位且不向前补足；当日有效。因此每只证券样本最早从其第 7 个有效会话开始，全样本第一个合格日为 2022-01-11。

**结果**【事实】：已确认普通股 7,016 只：4,740 通过、2,257 因异常率 >5% 剔除（Nasdaq 1,827、NYSE 430）、19 暂缓。

## 6. 指数再平衡日与半日市排除

- **范围**【选择】（director 要求）：重大指数再平衡的**收盘执行日**整日排除（不是公告日或生效日），覆盖 NYSE 与 Nasdaq；退出训练/测试样本、10 日窗口有效计数、5% 分母及日内检查点预期量估计。单只股票的临时纳入/剔除不做市场级排除。
- **清单** `auction_data/reference/index_events.csv`：51 条，记录事件族、指数、公告日、执行收盘日、生效日、适用市场、官方来源、核验状态与备注。

| 事件族 | 条数 | 已核验 | 惯例 |
|---|---|---|---|
| S&P 季度再平衡 | 20 | 18 | 3/6/9/12 月第三个周五收盘；遇假日提前（2026-06-18，因 06-19 Juneteenth） |
| Nasdaq-100 年度重构 | 5 | 4 | 12 月第三个周五收盘 |
| Russell 美国指数重构 | 6 | 6 | 6 月第四个周五；2026 起另有 12 月（2026-12-11） |
| MSCI 指数评审 | 20 | 20 | 2/5/8/11 月，通常为当月最后交易日；3 次提前（2024-11-25、2025-08-26、2025-11-24，依官方公告） |

- 区间内 47 条事件落在 43 个不同交易日（同日多事件只排除一次）；另有 9 个半日市（13:00 收盘）。合计排除 52 个交易日，事件日屏蔽 221,449 个、半日市屏蔽 45,507 个已确认普通股的上市股票日。
- **未验证**【待核实】：2025-06-20 S&P 季度日期仅有 S&P/NZX 官方通知与美国方法论规则，按规则继续排除；2026-12-18 的 S&P 与 Nasdaq-100 为未来事件，在当前数据区间之外。不因"当季无成分股变化"取消排除【选择】。
- 半日市在观察层保留，收盘后检查点标 `not_applicable`。

## 7. 严格 PIT 与事后口径

| 口径 | 字段 | 使用的信息 | 用途 |
|---|---|---|---|
| 事后（ex-post） | `security_status`、`eligible` | 全历史（含未来）异常率 | 清洗与审计；**不能**直接用于严格历史回测 |
| 严格 PIT | `pit_n_expected`、`pit_n_anomaly`、`pit_eligible` | 只用 t 之前的会话；在 [max(请求起点, 上市起点), t−1] 上扩展计算异常率 ≤5% + 10 日 6 有效 + 当日有效 | 按预测时可见信息构造样本；下游可按任一截止日的计数冻结股票池 |

- 假设 t−1 的数据在 t 日可见【选择】；Compustat 日频与身份表是修订后快照，无首次发布时间，PIT 只能在交易日期层面保证【事实】。
- 新股上市初期分母小，一个异常日即可能暂时超过 5%（例如第 12 个会话前有 1 个异常 → 不合格），不另加最低历史门槛【选择】。
- 两种口径在应有股票日上的分歧为 3.6%：仅事后合格 167,223 个（均因 PIT 早期异常率），仅 PIT 合格 52,772 个（事后整股剔除的证券在其早期仍满足 PIT）。
- 字段可用时点见 `reference/field_dictionary.csv` 的 `available_at`：当天竞价量、竞价价、打印次数、`continuous_volume`、`trf_close_volume` 与 `cshtrd` 都是**收盘后**字段，不能作 t 日特征。

## 8. 研究纳入

- `research_included_expost = eligible`；`research_included_pit = pit_eligible` 且证券不在暂缓之列【选择】。
- `research_sample.csv.gz` 只含二者任一为真的行：4,680,337 行（事后 4,628,219，PIT 4,513,114，两者皆是 4,460,996），PIT 纳入涉及 5,126 只证券，日期 2022-01-11 至 2026-11-19。
- 当前研究使用 `research_included_pit`；每个实验记录筛选条件、处理版本目录与 Git 提交【选择】。

## 9. 暂缓证券【选择】

124 只 `unresolved` 证券本次研究不使用（`research_status='deferred'`），保留标识与原因，每次处理从输入重新评估：

| 原因 | 只数 | 说明 |
|---|---|---|
| `unknown_security_type` | 105 | 104 只在区间内有 N/Q 上市区间但 Compustat 日频无记录（93 只区间覆盖窗口但无日频、6 只区间在 2022 年首周结束、3 只 2026-11 新上市、2 只为 iid 90+）；1 只（SLGB）有 277 天日频但 `tpci` 缺失 |
| `ticker_history_gap` | 19 | 已确认普通股，但上市区间内有无有效 TIC 的交易日（TIC 生效晚于 EXCHG，1–526 天不等） |

严格 `pit_eligible` 不被暂缓状态改写（例如 TORO 保留 654 行严格 PIT 资格），但这些行不进入研究样本。暂缓证券不阻止处理或更新报告完成。

## 10. 拆股换算

- **规则**【事实，已用真实数据验证方向】：`ajexdi` 是累计调整因子，拆股前较大（NVDA 2024-06-10 10:1 拆股，由 10 变为 1）。历史日 s 的原生股数换算到预测日 t：`vol_s × ajexdi_s / ajexdi_t`。只用比值，下载后的新拆股会同比放大所有因子而相互抵消，因此满足 PIT。同日量不换算。
- **分段**【选择】：因子变化日的收盘价比与因子比偏差 >20%，或因子缺失/非正 → `split_unresolved`，`split_segment_id` 断开，跨断点不换算。
- **验证样例**【事实】：NVDA 2024-06-10、AVGO 2024-07-15、WMT 2024-02-26、CMG 2024-06-26 为拆股；SIRI 2024-09-10（1:10）、AMCR 2026-01-15（1:5）为合股；HON 2026-06-29 因子减半而价格不变（分拆调整，非股数拆分）→ 正确标为未决。
- **规模**：561 行 `split_unresolved`（普通股中 514 行，多为仙股合股当日价格异动）；21,319 行"无因子变化的价格或股数跳变"只列入报告，不断开分段。处理阶段只提供换算所需字段，不输出复权后的特征。

## 11. 日内检查点

- 长表 `us_wrds_taq/checkpoints_research.csv.gz`，键 `(security_id, date, checkpoint)`，只含唯一匹配的上市普通股日：36,937,908 行，其中 176,840 行为半日市收盘后的 `not_applicable`。
- 适用检查点全部有限且非负，累计量无倒退【事实】；以当日最终量为分母的比例只作诊断（`_diag`）。
- 典型日（2026-11-19）累计成交到 15:50 约为全天连续成交的 86%（NYSE 中位数）至 91%（Nasdaq）；竞价占比中位数 NYSE 12.4%、Nasdaq 2.6%（探索性统计，非产物字段）。

## 12. 处理结果【事实】

| 产物 | 行数 | 键 |
|---|---|---|
| `us_wrds_daily/daily_clean.csv.gz` | 10,113,152 | security_id, date |
| `us_wrds_taq/daily_summary.csv.gz`、`labels_clean.csv.gz` | 13,942,671 | taq_symbol, date |
| `us_wrds_quality/stock_day_quality.csv.gz` | 6,316,893 | security_id, date（已确认普通股的上市区间） |
| `us_wrds_quality/eligible_stock_days.csv.gz` | 4,628,219 | 事后合格 |
| `us_wrds_quality/research_sample.csv.gz` | 4,680,337 | 研究纳入 |
| `us_wrds_taq/checkpoints_research.csv.gz` | 36,937,908 | security_id, date, checkpoint |
| `us_wrds_quality/security_quality.csv` | 13,147 | security_id |

**筛选漏斗**（股票日，按固定顺序只计一次）：

| 步骤 | 股票日 | 本步移除 |
|---|---|---|
| 已确认普通股的上市区间 | 6,316,893 | — |
| 身份可用 | 6,314,107 | 2,786 |
| 去除事件日与半日市 | 6,047,151 | 266,956 |
| 事后 5% 整股筛选（含暂缓证券 12,371） | 4,677,515 | 1,369,636 |
| 前 10 日至少 6 有效 | 4,643,892 | 33,623 |
| 当日有效 → 事后合格 | 4,628,219 | 15,673 |

注：`report/funnel.csv` 的 `with_listing_boundary` 行证券数 6,997 不含 19 只暂缓证券，但该行股票日包含它们的上市股票日（分别在身份、屏蔽和整股筛选步骤移除，其中身份缺失 2,786、整股未决 12,371）。这只是报告标注问题，数据无误，已列入 OPEN_ITEMS。

**按市场**（应有股票日）：

| 市场 | 应有股票日 | 证券 | 有效日占比 | 事后合格 | PIT 研究纳入 |
|---|---|---|---|---|---|
| Nasdaq | 3,904,949 | 4,779 | 88.9% | 2,713,569 | 2,634,525 |
| NYSE | 2,144,871 | 2,391 | 94.9% | 1,914,650 | 1,878,589 |

**每日保留数**（常规交易日）：事后合格中位数 3,954（2022 年约 4,255，2026 年约 3,685），PIT 中位数 3,809；上市普通股中位数 5,014。2022-01-03 至 01-10 为窗口暖启动，保留数为 0。图与底表：`report/daily_counts.png`、`daily_counts.csv`。

## 13. 质量检查【事实】

- **raw 审计**：1,286 个分区的指纹、行数、列与交易日覆盖全部通过；无来源内部缺日。
- **发布校验**：每个产物写出后读盘核对主键唯一与行数，记录 SHA256；全部成功后才原子切换 `wrds_current`，失败保留上一版本。
- **计数对齐**：`report/security_status_summary.csv`：13,147 = 6,026 非普通股 + 105 类型不可判定 + 7,016 已确认普通股；7,016 = 4,740 + 2,257 + 19。
- **暂缓证券**：124 只均不在 `research_sample` 中。
- **可复现**：同一 raw 快照、配置与版本重复处理逐字节一致（gzip mtime 固定）；首次提交前用干净副本独立重建，21 个数据产物 SHA256 与基线一致；结构调整后用新代码再次独立重建，处理指纹与数据产物同样一致。图表与 `summary.json` 的上一版本差异字段按复现文档排除。
- **测试**：48 项离线测试，覆盖续传与修订、标签与场所、转板与迁出、屏蔽窗口、5%/6-of-10 边界、PIT 不泄露、拆股方向与真实样例、检查点、暂缓、比较工具与 CLI。

## 14. 已知限制

- 事后 5% 判定使用全历史，不是严格历史时点股票池；严格回测用 PIT 字段【事实】。
- Compustat 日频与身份表是修订后快照，无首次发布时间；`tpci` 为当前快照【事实】。
- Compustat↔TAQ 为历史 ticker 匹配，非官方永久链接【事实】。
- 标签缺失比例 Nasdaq 高于 NYSE，2,257 只证券（Nasdaq 为主）被事后剔除；缺失按市场与规模的分解尚未做【待核实】。
- 竞价打印时间戳若早于收盘会同时计入 `continuous_volume`，是否存在需 WRDS 抽查【待核实】。
- REIT/SPAC 无法标记【待核实：需下载 Compustat company 表】。
- 日内数据只有累计量检查点，无价格路径、VWAP 或收盘不平衡数据【事实】。
- 事件清单 3 条未验证（§6）【待核实】。
- 日内 TAQ 字段为小数股数（零股）【事实】。

## 15. 复现与更新

按 [复现与差异核查](REPRODUCIBILITY.md)：

1. 安装 `requirements.txt`，运行 `python -m unittest`；
2. `python -m auction_data check` 确认权限；小区间 `update` 冒烟；
3. 全量 `update`（或 `update --download-only` + `process`），`audit`；
4. 核对 manifest、`report/security_status_summary.csv`、`funnel.csv`、`summary.json`；
5. 更新前保留 raw 快照，更新后用 `compare` 比较 raw 与处理版本，按复现文档的解释对照表说明差异；
6. 修改规则须递增 `PROCESS_VERSION`；修改事件清单会自动触发重建。

## 16. 尚待核实与后续（摘自 [OPEN_ITEMS](OPEN_ITEMS.md)）

- 2025-06-20 S&P 美国官方生效通知；2026-12-18 两条未来事件。
- 图表 PIT 曲线改用 `research_included_pit`；funnel 暂缓证券标注。
- REIT/SPAC 行业表；竞价与连续成交是否重叠；EXCHG `24` 场所代码。
- 标签缺失与事后剔除的市场/规模分解。
