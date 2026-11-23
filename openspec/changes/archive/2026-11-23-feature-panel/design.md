## Context

- 输入：processed 版本 `wrds-process-v3`，当前 `Data/processed/wrds_current → .wrds_versions/4eb19f3a851541f5ba1d14a1dc7c445c`，指纹 `cc72b366…`。`stock_day_quality` 为 7,016 只已确认普通股全部上市会话（6,316,893 行，含屏蔽日、无效日）；价格/股本在 `daily_clean`；检查点在 `checkpoints_research`。
- 已核实：pit 行价格、股本、市值无缺失；标签从不为 0（无竞价为 NaN）；TAQ 全天量 / Compustat `PX_VOLUME` 中位数 0.987；2022-04 后 pit 行 4.66% 的 t−1 不可用，其中 4.48% 因 t−1 为屏蔽日；`split_applied` 1,675 行，pit 行中 682 行恰为拆股日；拆股日股本同日更新约 91%；154 只证券转板。
- 约束：不改 `auction_data` 代码与 processed 产物；特征层不做任何拟合（标准化、winsorize、填补、聚类均属模型层）。

## Goals / Non-Goals

**Goals:** 逐行 as-of 特征只依赖 t 之前的观测、t 日日历与 `f_t`；一次计算服务任意时间切分与 walk-forward；特征表自带目标与筛选元数据，模型层无需再读 processed；命名区分严格滞后与最近有效；提供聚类所需画像（未拟合）；已发布版本不可改写。

**Non-Goals:** 扩展特征族（E1–E5）、簇级特征、财报/指数成分/VIX、盘中价格类特征、LSE。

## Decisions

### D0 固定输入版本

- 构建开始时把 `wrds_current` 解析为实际版本目录（`.wrds_versions/<id>`），此后只从该目录读取；可用 `--processed-dir` 显式指定版本目录。
- 读取前用 processed manifest 中的 sha256 校验 `stock_day_quality`、`daily_clean`、`checkpoints_research` 与 `manifest.json` 自身摘要；构建结束时再次确认 processed manifest 指纹与输入文件摘要未变，否则构建失败且不发布。
- 特征 manifest 记录：解析后的目录、processed 版本与指纹、三个输入文件的 sha256。

### D1 全面板、可用条件与连续市场区间

记 `W(t,w)` 为 t 之前的 w 个 XNYS 会话（t−w…t−1）。屏蔽日、未上市会话都占位，不回补，不向前延伸。

连续市场区间 `interval_id`：证券在连续的上市会话上保持同一 `market` 时属于同一区间；遇到未上市会话（退市后重新上市等）或 `market` 变化即开始新区间。`market` 与区间来自 `sec_idhist` 生效区间，按开盘前已知处理。

| 条件 | 定义 | 断点要求 |
|---|---|---|
| U_A 竞价 | `valid_day_s`（复用已验收定义：标签与成交量 >0、身份唯一、未屏蔽、上市区间内） | 同一连续市场区间 ∧ `seg_s = seg_ref_t` |
| U_V 成交量 | 上市、未屏蔽、`PX_VOLUME_s` 有限 >0 | 同上 |
| U_C 检查点 | `applicable`、`volume_valid`、未屏蔽 | 同上 |
| U_P 价格 | `PX_LAST_s` 有限 >0（屏蔽日可用） | 只用于严格 t−1 的市值，市值为同日价×股本，不需区间与分段条件 |

`seg_ref_t` = t−1 及以前最后一个已知 `split_segment_id`。研究纳入标志、`research_status`、`security_status`、`eligible` 不参与任何历史计算。备选"先按 `research_included_pit` 筛选再滚动"被否决：会压缩日期并把事后状态带入历史。

### D2 预测时拆股、缺失因子与股本

- 换算：股数 `V_s·f_s/f_t`，价格 `P_s·f_t/f_s`；对每个 t 直接按窗口计算，结果在浮点精度内精确。
- 历史断点用 `seg_ref_t`：其值只由 ≤ t−1 的因子与价格决定，开盘前可得。
- A1（未验证的可得性假设）：t 日拆股生效与比例开盘前已公告，故使用 `f_t`。输出 `split_on_t`（`f_t` 与 t 之前最后已知因子不同）供稳健性剔除。
- 缺失因子：
  - `f_t` 缺失或 ≤0：沿用 t 之前最后已知因子（假设当日无拆股），标 `factor_carried`；从未有已知因子时所有需换算的特征为 NaN，标 `factor_unknown`。
  - 历史槽位 s 的 `f_s` 缺失或 ≤0：该槽位不可用于换算族（竞价、成交量、检查点）。processed 已在缺失因子行及其后一行断开分段，因此不会跨缺失因子换算。
- 股本与价格：`logmcap_lag1` 只用 t−1 当日的 `PX_LAST` 与 `shares_outstanding`（ADR 已按 `adrrc` 换算），任一缺失或 ≤0 即 NaN，不向前沿用。拆股日 `cshoc` 未同日更新（约 9%）会使市值跳变，不做修正，报告列出 `split_on_t` 前后 |Δlogmcap| > ln 1.8 的行数。
- t 日 `split_status ∈ {split_unresolved, factor_missing}` 依赖 t 日收盘或 t 日行：只输出 `expost_split_issue_t`，不进入特征或样本筛选。
- 因子为下载快照：比值抵消下载后拆股；历史修订无法排除。
- 拆股一致性的准确表述：同日两字段之比或积（竞价占比、竞价金额、换手类）在两字段当日同一口径时不受纯拆股单位变化影响；跨日的量（`a_*`、`d_*`、`mu_b`）依赖 `f` 换算，只在同一分段内且 A1 成立时一致（log1p 作用在换算后的量上，换算本身精确）；`e_s = ln(1+V^A_s) − a_ma22_s` 以 s 日股数为口径，log1p 的 +1 项使其在一致拆股前后只近似不变，偏差约 `1/V^A`（实测合成数据 V^A≈4×10⁴ 时约 1e-6；真实 pit 行竞价量 1% 分位仅 20 股，低量股票窗口含拆股时偏差不可忽略）；跨 `split_unresolved` 断点不保证一致，而是断开历史（相应特征按最少观测规则变为 NaN）；市值依赖价格与股本同日更新。

### D3 命名

- `x_lag1`：严格 t−1，t−1 不满足族条件即 NaN。
- `x_last`、`x_last_age`：W(t,5) 内最近一个可用观测及其距 t 的会话数（1–5）。
- 不得把最近有效值命名为 `lag1`。

### D4 日频与盘中核心（第一版）

| 名称 | 定义 | 窗口/最少 | 角色 |
|---|---|---|---|
| `a_last`, `a_last_age` | 最近 U_A 观测的 `log1p(V^A_s·f_s/f_t)` | W(t,5)/1 | core |
| `a_ma5`, `a_ma22` | U_A 换算后 log1p 均值 | 5/3, 22/15 | core |
| `d_last`, `d_last_age` | 最近 U_V 观测的 `log(PX_VOLUME_s·f_s/f_t)` | W(t,5)/1 | core |
| `d_ma22` | U_V 换算后 log 均值 | 22/15 | core |
| `logmcap_lag1` | 严格 t−1：`log(PX_LAST·shares_outstanding)` | 1 | core |
| `dow`, `month_end`, `quarter_end`, `opex` | XNYS 日历；opex = 当月第三个周五，休市则前一会话 | 事先已知 | core |
| `a_lag1`, `d_lag1`, `a_n22`, `d_n22` | 严格 t−1 版本；22 会话内可用槽位数 | — | diagnostic |
| `C_b` | t 日截至 b 的连续成交量（原生股数），需 U_C | — | intraday |
| `mu_b` | W(t,20) U_C 观测 `log1p(C_{s,b}·f_s/f_t)` 均值 | 20/12 | intraday |
| `q_b` | `log1p(C_b) − mu_b` | — | intraday |

评估必需 6 列 `core_required` = {`a_last`, `a_ma5`, `a_ma22`, `d_last`, `d_ma22`, `logmcap_lag1`}；输出 `core_required_complete` 供模型层与报告使用，特征层不据此删除任何行。

min_periods 约 60%，与"10 日 6 有效"同比例；6 月 22 会话内可能有 3 个屏蔽日，80% 不可行。

### D5 月初行为画像（未拟合）

m 为每月第一个会话；窗口 W(m,126)。

| 名称 | 公式 | 有效要求 |
|---|---|---|
| `pf_lshare` | `mean ln(V^A_s / PX_VOLUME_s)`；分母 Compustat 当日全天量；>1 比值保留，交由训练期 winsorize | ≥ 60 个观测 |
| `pf_dev_std` | `1.4826·MAD(e_s)`（围绕窗口内自身中位数），稳健尺度 | ≥ 60 个 e |
| `pf_dev_ac1` | 截断 e 后，对紧邻会话配对求 Pearson 相关 | ≥ 50 对，且 `pf_dev_std > 1e-6` |
| `pf_dollar_auc`（可选规模扩展） | `mean ln(V^A_s × PX_LAST_s)` | ≥ 60 个观测 |

计算规格（逐证券、逐月，只用本股本窗口数据，无跨股票或跨期拟合）：

1. 观测集合 `S_m` = W(m,126) 内满足 U_A 且与 m 处于同一连续市场区间的会话；`n_A = |S_m|`。画像不另加分段截断：`pf_lshare`、`pf_dollar_auc` 为同日比值/乘积，一致拆股下精确不变；`e_s` 中的 `a_ma22_s` 已按 s 的分段规则计算，`pf_dev_std`、`pf_dev_ac1` 在一致拆股下近似不变（偏差约 `1/V^A`，见 D2）。
2. `pf_lshare = mean_{s∈S_m} ln(label_s / PX_VOLUME_s)`，要求 `n_A ≥ 60`。
3. `E_m = {e_s : s ∈ S_m 且 a_ma22_s 非缺失}`，`e_s = ln(1+label_s) − a_ma22_s`；`n_e = |E_m| ≥ 60`。
4. `med = median(E_m)`；`MAD = median_{s}(|e_s − med|)`；`pf_dev_std = 1.4826 × MAD`。若 `pf_dev_std ≤ 1e-6` → `degenerate_dispersion`。
5. 截断：`ẽ_s = min(max(e_s, med − 4·pf_dev_std), med + 4·pf_dev_std)`，界限为本股本窗口值。
6. 配对集合 `P_m = {s : e_s ∈ E_m 且 e_{prev(s)} ∈ E_m}`，`prev(s)` 为紧邻的上一个 XNYS 会话（不是上一个有效观测）；`n_p = |P_m| ≥ 50`。
7. `pf_dev_ac1 = Pearson(x, y)`，`x = (ẽ_s)_{s∈P_m}`，`y = (ẽ_{prev(s)})_{s∈P_m}`，两向量各用自身均值与总体标准差（ddof=0）。任一向量方差为 0 → `degenerate_dispersion`。
8. 诊断列（不进入模型）：`pf_dev_std_plain = std(E_m, ddof=1)`、`pf_dev_ac1_plain`（未截断 e 的同一配对 Pearson）、`pf_dev_ac1_spearman`、`n_A`、`n_e`、`n_p`。

默认三项任一不满足，三项同时置 NaN，`profile_status` 记录首个原因：`not_listed`（m 当日未上市）、`interval_break`（因转板或重新上市，当前连续区间内观测不足，而窗口内全部观测足够）、`insufficient_history`、`insufficient_pairs`、`degenerate_dispersion`；满足为 `ok`。画像行覆盖证券在该月有任一面板行的所有 证券-月。可得时点：m 开盘前（只用 m 之前会话，假设 t−1 数据在 t 日可见）。

阈值理由：自相关标准误的粗略量级（i.i.d. 近似 `(1−ρ²)/√n`，仅作参考，序列相关与截断会改变实际精度）为 50 对约 0.14、100 对约 0.1；以 60/50 起步，新股约 75 个有效日（约 3.5 个月）后才有画像，2022 年 4 月全部、5 月部分为缺失，保留并在报告中披露。

### D6 输出、元数据与不可改写的版本

- 位置：`Data/features/.versions/<fingerprint 前 16 位>/`，`Data/features/current` 符号链接原子切换。
- 表：`daily/<MARKET>/<YEAR>.parquet`（主键 `security_id`,`date`，行集合 = `stock_day_quality`）；`intraday/<MARKET>/<YEAR>.parquet`（主键加 `checkpoint`，行集合 = `checkpoints_research`）。不用 `market=…/year=…` 形式的 hive 目录：文件内已含 `market` 列，hive 读取会因同名分区键类型冲突而失败；`profiles/profiles.parquet`（主键 `security_id`,`month`）；`feature_dictionary.csv`；`manifest.json`；`report/`。
- daily 表的目标与筛选元数据（逐值等于 processed，模型层只读特征表即可构造样本）：`y`、`label`、`label_status`、`valid_day`、`mask_reason`、`pit_eligible`、`research_status`、`research_included_pit`、`research_included_expost`、`split_status`；仅供报告的事后字段 `security_status`、`exclusion_reason`（字典标注 ex-post，模型层不得作为特征）；派生标志 `split_on_t`、`factor_carried`、`factor_unknown`、`expost_split_issue_t`、`interval_id`（证券内连续市场区间序号）、`interval_start`（t 为该区间首日）、`core_required_complete`。profiles 表同样输出画像所用区间的 `interval_id`，供模型层判断行与画像是否同区间。
- 指纹 = sha256(processed 指纹 + 输入文件摘要 + `FEATURE_VERSION` + config + 特征代码摘要)，相同则复用，不写任何文件。
- 发布：整个版本先写入 `.versions/.tmp-<uuid>`，校验通过后改名为最终目录，再切换 `current`。已发布目录此后不再被任何命令写入：`verify` 只读；`report` 命令从已发布版本重算到 `Data/features/reports/<version>/`，不改动版本目录及其 manifest。
- 确定性按排序后内容哈希判定（parquet 字节不保证一致）。版本切换复用 `storage` 工具，不改 `auction_data.publish`。

### D7 验收口径

- 不变性测试（截断、未来扰动、个股隔离、同日独立、研究标志无关）：同一代码路径比较，要求逐位相等且 NaN 位置相同。
- 向量化实现与逐行参考实现的对拍：NaN 位置相同；整数、布尔、`*_age`、计数列逐值相等；浮点列 `|Δ| ≤ 1e-12 + 1e-12·|ref|`（求和顺序不同允许舍入差）。
- 核心完整率不设硬门槛：报告 2022-04 后 pit 行 `core_required_complete` 比例及不完整原因分解（按列、按 `*_last_age`、拆股/区间断点、因子缺失）。依据此前抽样预期约 99.9%；低于 99.5% 时作为复核触发条件，须在验收报告中解释后才能进入模型阶段，但不使构建失败。
- processed 不变：构建前后 processed manifest 指纹与三个输入文件 sha256 相同。

## Risks / Trade-offs

- [A1 拆股公告假设未验证] → `split_on_t` 稳健性剔除并在报告列出。
- [价格族使用屏蔽日与成交量族口径不同] → 字典逐列标注可用条件。
- [`a_last` 在事件日次日取 t−2] → 同时输出 `a_last_age` 与严格 `a_lag1` 诊断列。
- [画像阈值导致早期与新股大量缺失] → 报告每月缺失比例；缺失不改变评估样本（模型层规则）。
- [ac1 估计噪声] → 稳健截断、最少 50 对、输出普通/Spearman 诊断与相邻窗口重测相关。
- [股本更新滞后导致市值跳变] → 不修正，报告计数。
- [全面板 parquet 体积] → 按市场/年分区、zstd；只存第一版列。

## Migration Plan

新增包、`pyarrow` 依赖与 `Data/features/`；不触碰 processed。回滚即删除 `Data/features/` 与新包。

## Open Questions

无。画像阈值 60 个观测 / 50 对已确认（2026-11-22）。
