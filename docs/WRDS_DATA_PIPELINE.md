# WRDS 数据下载、处理与质量筛选

## 入口与依赖

安装根目录 `requirements.txt`；已在本地核对 Python 3.13、Pandas 2.2.3、psycopg2-binary 2.9.13、exchange_calendars 4.13.2、matplotlib 3.10.8（质量报告图）。模块使用原生 psycopg2 PostgreSQL 协议，不要求安装 `wrds` 包。已检查本机 WRDS 3.5.0 的连接代码：其自动回退路径可能提示创建 `.pgpass`，因此新模块不用这条路径。

```bash
python -m auction_data update --start 2022-01-03 --end 2026-10-16
python -m auction_data update --refresh-sessions 5
python -m auction_data update --refresh-start 2022-01-03
python -m auction_data update --download-only
python -m auction_data process
python -m auction_data audit
```

不指定结束日期时，使用纽约当前日期，依据交易日历只下载已收盘并经过 30 分钟的会话；分别探测 daily/TAQ 发布范围。默认最近 5 个已发布交易日涉及的分区重新下载；更早修订需显式刷新。`--refresh-sessions 0` 可关闭近期刷新。支持 `--markets NYSE`、`--data-dir /path/to/Data`、公开 WRDS 主机/端口和连接超时配置。默认同时覆盖 NYSE/Nasdaq，保留原生证券类型。

Python 入口为 `update_data`、`process_data`、`audit_data`（从 `auction_data` 导入）。`update_data` 支持调用者提供 DB-API `connection`（调用者负责关闭），或 `credentials_provider` 回调返回仅包含 `user`、`password` 的字典。不要将真实凭证写入脚本或 notebook。`process_data` 和 `audit_data` 不联网。`update_data` 返回报告；只有 `report['complete']` 为真才代表本次请求及质量层均完成。CLI 成功返回 0，部分完成/待发布/错误返回 2；暂缓（unresolved）证券不影响返回值。复现、快照比较与差异解释见 [复现与差异核查](REPRODUCIBILITY.md)。

`python -m auction_data check` 为只读登录、来源权限与发布日期探测，不下载数据。

## 认证与隐私

交互运行时询问自己的用户名和隐藏密码；根据 WRDS 账号设置自行完成 Duo 批准。原生连接支持服务器要求的认证，但模块不能代替用户批准、不能保证每种机构登录策略均支持；失败时明确报告，不绕过认证。连接超时默认 60 秒，单查询 15 分钟，认证最多 2 次尝试；连接中断最多重连重试一次。进程内重连复用认证上下文，结束后清理引用，不写凭证文件。

默认不使用已有 `.pgpass`；用户可显式提供 `--use-pgpass`，仍需提供/输入自己的用户名。非交互场景须通过注入连接/认证回调或显式已有本地凭证配置认证，否则快速失败。源码、日志、元数据不记录个人账号、密码或 DSN，底层异常不原样输出。`Data/`、本地 `.env` 与认证文件由 Git 忽略，不读取本机凭证以进行隐私检查。

## 来源和分区

- 日频：`comp_na_daily_all.secd`，历史标识：`sec_idhist`。历史 `EXCHG` 11/14 + 当日 USD 筛选，无当前 Bloomberg 股票池筛选。日频主键 GVKEY/IID/date。
- TAQ：`taqm_YYYY.ctm_YYYYMMDD`，服务器端按股票日汇总。保留既有条件码包含 `6` 的收盘竞价口径，按 N/Q/P/A/Z 分场所保存竞价量（`xvol_*`）。raw 字段 `auction_volume`/`listing_ex` 为最大竞价场所及其量（推断，不是官方上市身份），仅用于对账；研究标签按历史上市交易所取 `xvol_n`/`xvol_q`（见下文研究样本口径）。
- raw 路径：`raw/wrds/daily/months/YYYYMM.csv.gz`、`raw/wrds/daily/history.csv.gz`、`raw/taq/days/YYYYMMDD.csv.gz`，相邻 `.json` 记录来源、区间、schema/query 版本、行数、列与 SHA256。

`pipeline_plan.json` 保存完整预期 time chunk 清单，每块有边界、预期交易日、状态、返回行数及校验。日频按月、TAQ 按日；既查整块遗漏，也查块内交易日缺口，不凭首末日期或文件存在判断完成。旧无版本元数据分区须重取。文件损坏、来源/配置/版本变化也须重取。月内扩展重取旧新区间并集，避免覆盖已有历史。来源列出但未能查询、空结果或源内部缺日不能算成功。

`update_report.json` 记录逐来源已发布范围、内部缺日、pending 日期、共同截止日和各块状态。源最大日期不是证券级完整性证明，已发布数据仍可能修订。下载失败保留有效分区，下次相同调用恢复；不要手工把失败块标为完成。源权限不足不会替换为 CRSP/Bloomberg。

## 处理输出和发布

| 相对 `Data/processed/` 路径 | 内容/主键 |
|---|---|
| `us_wrds_daily/daily_clean.csv.gz` | 日频原生观察表（含 `ajexdi`、`split_status`、`split_segment_id`、`is_reit`/`is_spac`=NA）；security_id/date |
| `us_wrds_taq/daily_summary.csv.gz` | 全部 TAQ 符号的汇总（宽表 `cumvol_*`、`xvol_*`）；taq_symbol/date |
| `us_wrds_taq/labels_clean.csv.gz` | 各场所竞价量、最大场所竞价量、推断场所、价格、打印次数；taq_symbol/date |
| `us_wrds_taq/checkpoints_research.csv.gz` | 唯一匹配的上市普通股日内检查点长表，半日市收盘后检查点为 `not_applicable`；security_id/date/checkpoint |
| `us_wrds_quality/stock_day_quality.csv.gz` | 上市区间内全部普通股证券日：标签、对账、屏蔽原因、ex-post 与 PIT 资格、排除原因 |
| `us_wrds_quality/research_sample.csv.gz` | 本次研究训练样本：`research_included_expost` 或 `research_included_pit` 为真的证券日；暂缓（deferred）证券不进入。单个实验只选其中一个纳入标志 |
| `us_wrds_quality/eligible_stock_days.csv.gz` | ex-post 合格证券日 |
| `us_wrds_quality/security_quality.csv` | 每只证券的应有日数、异常数、异常率、状态（retained / excluded / non_common_stock / outside_window / unresolved）、原因及 `research_status`（in_scope / deferred / not_applicable） |
| `reference/index_events.csv`、`reference/field_dictionary.csv` | 本次使用的事件清单副本；字段来源、单位、口径和 `available_at` |
| `report/` | `daily_counts.csv/png`、`funnel.csv`、`security_status_summary.csv`（证券状态逐层对齐）、`exclusion_reasons.csv`、`excluded_securities.csv`、`unresolved_securities.csv`、`label_reconciliation.csv`、`label_status.csv`、`outside_venue_crosses.csv`、`split_issues.csv`、`unverified_events.csv`、`summary.json`（PIT 与 ex-post 差异、与上一版差异） |

旧版 `us_wrds_taq/checkpoints_clean.csv.gz`（全部 TAQ 符号、以 taq_symbol 为键的约 9,500 万行长表）已由 `checkpoints_research` 取代；全部符号的检查点仍以宽表形式保留在 `daily_summary`。

`security_id=GVKEY:IID`。日频 `PX_OPEN/HIGH/LOW/LAST` 为 USD 原报价；PRCSTD=4 的 high/low 移至 closing ask/bid，原值和状态保留。CSHTRD 为 Compustat 报告的股数；普通证券股数取 CSHOC，ADR（TPCI F/S）取 CSHOC/ADRRC。`close_times_volume_usd` 仅为价格乘成交量代理。

TAQ 检查点为 10:00、12:00、14:00、15:00、15:30、15:50（纽约时间）。早期累计量为 0 可以合法，负数/非有限值和累计量倒退有单独标记。TAQ 成交量含零股，为小数；竞价量为整数。每个字段的可用时点（`t-1 close` / `t intraday` / `t post-close`）见字段字典；当天竞价量、`continuous_volume`、`trf_close_volume`、`cshtrd` 都是收盘后字段。

先在 `.wrds_versions/<id>/` 构建产物并读盘核对主键与行数，写入输出 SHA256、输入指纹和规则版本。验证完成后切换 `wrds_current` 符号链接，各公开目录通过它读取同一代。指纹 = raw 分区指纹 + 配置 + `PROCESS_VERSION` + 事件清单 SHA256；同一输入复用现有版本，gzip 输出逐字节可复现。**修改处理规则代码时必须递增 `common.PROCESS_VERSION`**，否则会复用旧产物；`RAW_VERSION` 只用于 raw 分区元数据，递增它会使全部 raw 分区重新下载。失败不切换有效版本。消费者若跨多个文件长时间读取，应先解析 `wrds_current` 的实际代目录并固定该路径。模块不自动清理历史版本。

## 研究样本口径

1. **普通股**：只保留 Compustat `tpci='0'`。`%` ETF、`1`/`G` 优先股、`2` 权证、`4` units、`7` 封闭式基金、`F`/`S` ADR、`R` 债券型证券（不是 REIT）均排除。`tpci` 为当前快照。REIT/SPAC 无行业字段可识别，随普通股纳入，标记列为 NA。
2. **标签**：当日历史上市交易所的收盘竞价股数，EXCHG=11 取 `xvol_n`，EXCHG=14 取 `xvol_q`；缺失即标签缺失，不回退其他场所或最大场所。`venue_reconciliation` 分为 consistent / venue_conflict / listing_cross_missing / identity_unresolved。竞价打在 N/Q/P/A/Z 之外的证券日列于 `outside_venue_crosses.csv`（如 2026-10 起 EXCHG 变为 `24` 的 ET 等，这些证券在变更日退出上市区间，不计异常）。
3. **屏蔽日**：`auction_data/reference/index_events.csv` 中的重大指数再平衡执行收盘日（S&P 季度、Nasdaq-100 年度、Russell、MSCI）和 XNYS 半日市，退出研究样本、10 日窗口有效计数和 5% 分母，在观察层保留并标 `mask_reason`。清单为人工核验的参考数据，`verified=false` 的条目仍剔除，列于 `unverified_events.csv`。
4. **有效日**：日频成交量与标签都有限且 >0、身份唯一、非屏蔽日。
5. **ex-post 资格**：全历史异常率（分母为请求范围 ∩ N/Q 上市区间 ∩ 共同已验证覆盖 − 屏蔽日）严格 >5% 整只剔除；t 日之前 10 个 XNYS 会话中至少 6 个有效日（屏蔽日占位、不向前补足）；当日有效。该结果使用未来数据，标为 `ex_post_full_history_not_point_in_time_backtest`。
6. **研究纳入与暂缓**：`unresolved` 证券在本次研究中暂缓使用（`research_status='deferred'`），保留标识和原因（`unknown_security_type`：窗口内无日频或类型代码缺失；`ticker_history_gap`：上市区间内有无有效 TIC 的交易日；`ambiguous_identity`；`missing_effective_listing_market_boundary`；`zero_denominator`），每次处理从输入重新评估。训练只用 `research_included_expost`（= `eligible`）或 `research_included_pit`（= `pit_eligible` 且非暂缓）。暂缓证券不阻止 `process`/`update` 完成；`quality_status` 只表示处理成功，暂缓数见 manifest 的 `deferred_securities`、`deferred_reasons`。`update` 仍要求原始数据完整且无待发布交易日才报告完成。
7. **严格 PIT 资格**：`pit_n_expected`、`pit_n_anomaly` 只计 t 之前的会话，`pit_eligible` 要求 PIT 异常率 ≤5%、10 日 6 有效且当日有效。下游可按任意训练截止日取计数冻结股票池。假设 t−1 的数据在 t 日可见。`pit_eligible` 不受全窗口证券状态改写，单独使用它不是本次研究的训练筛选。
8. **拆股**：历史日 s 的原生股数换算到预测日 t：`vol_s × ajexdi_s / ajexdi_t`，只在同一 `split_segment_id` 内使用（`splits.to_basis`）。只用比值，因此与下载后的拆股无关。`ajexdi` 变化而收盘价比与因子比偏差 >20%（如 HON 2026-06-29 分拆调整）标 `split_unresolved` 并断开分段；未伴随因子变化的价格或股数跳变只列入报告。
9. 上市/市场边界使用 EXCHG 生效区间，ticker 使用 TIC 生效区间；身份链接为基于历史 ticker 的可审计匹配，**不是官方永久 TAQ 链接**。有 N/Q 区间但窗口内无日频记录（或类型代码缺失）的证券类型不可判定，标 `unresolved` 并暂缓。

### 后续项

- 下载 Compustat company 表（SIC/GICS）以标记 REIT（6798）与 SPAC（6770）。
- WRDS 一次性核验：竞价打印是否早于收盘时间戳而被 `continuous_volume` 重复计入；EXCHG `24` 对应的场所代码。
- 事件清单：2025-06-20 S&P 季度日期仅有 S&P/NZX 官方通知和美国方法论规则，未找到美国指数官方生效通知，保持排除且 `verified=false`；2026-12-18 S&P 与 Nasdaq-100 为未来事件，官方公告后核实。

## 测试

```bash
python -m unittest -v
```

`tests/` 使用合成账号、模拟 WRDS 连接和临时数据目录（48 项）：`test_pipeline`（续传、补洞、修订、发布滞后、失败恢复、凭证不泄露、写锁、CLI）、`test_auth_calendar`（认证、来源发现、XNYS 日历、raw/处理版本分离）、`test_identity_labels`（普通股范围、后缀映射、上市场所标签、转板与迁出）、`test_eligibility`（有效日、屏蔽日窗口、5% 边界、严格 PIT、暂缓）、`test_events_splits`（事件清单、半日市、拆股方向与真实拆股/合股样例；无本地数据时跳过真实数据用例）、`test_build_publish`（检查点、字段字典、端到端输出、可复现、事件清单变更重建）、`test_compare`（快照比较）。测试不执行真实登录或下载；真实源 schema、账户权限和 Duo 用 `python -m auction_data check` 与小区间 `update` 核验。

数据内容、处理结果与已知限制见 [数据报告](DATA_REPORT.md)；复现与差异核查见 [REPRODUCIBILITY](REPRODUCIBILITY.md)。旧下载/处理脚本、历史实验结果与迁移记录已在结构调整中删除，仅保留在 Git 历史（基线提交 `b50515d`）。
