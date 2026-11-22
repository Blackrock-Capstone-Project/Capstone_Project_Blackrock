## Context

`wrds-data-pipeline` 已实现 `update` / `process` / `audit`。当前 raw 状态：1,286 个分区全部通过离线审计，覆盖 2022-01-03 至 2026-11-19（2026-11-20 待 WRDS 发布）；`process` 尚未运行过，所以 `Data/processed/` 还不存在。

现有处理（`processing.py`、`quality.py`）已具备：版本目录构建 + 读盘核对 + `wrds_current` 原子切换、输入指纹复用、按证券流式生成日历网格、EXCHG/TIC 生效区间、`symbol_key`（`BRK.B → BRK/B`、优先股 `.PA → /PRA`）、10 日 6 有效与 5% 规则。

与新口径不符之处：

| 现状 | 问题 |
|---|---|
| 标签 = `auction_volume`（最大场所），`listing_ex ≠ market` 时 `match_status='ambiguous'` | 需改为按上市市场取 `xvol_n/q`，场所冲突是标签缺失而非身份歧义 |
| 股票池含全部 `tpci` | 需限定 `tpci='0'` |
| 无事件日 / 半日市屏蔽 | 需从窗口、分母、样本中屏蔽 |
| 只有 ex-post 资格 | 需增加 PIT 计数 |
| `adjustment_factor` 仅原样保留 | 需提供换算基础与 `split_unresolved` |
| 检查点表以 `taq_symbol` 为键 | 需以 `security_id` 为键 |

已用数据核实的事实：`tpci` 代码含义（`R` 为债券型证券而非 REIT）；`ajexdi` 为累计因子，NVDA 2024-06-10 由 10 变为 1；ET 等 10 只证券 2026-10/11 起 EXCHG 变为 `24`，同日 TAQ 竞价移出 N/Q；XNYS 2022 年至今半日市 11 天。

## Goals / Non-Goals

**Goals:**
- 在现有模块上实现 `auction-research-dataset` 规格，不改变下载、认证、分区校验。
- 一次 `process` 生成观察层、研究样本、检查点长表、字段字典、事件清单副本和质量报告。
- 规则全部版本化并进入指纹，变化触发全区间重建。

**Non-Goals:**
- 不改 `taq_sql.py`，不重新下载，不新增 WRDS 表（REIT/SPAC 标记、EXCHG `24` 场所代码核验列为后续）。
- 不训练模型、不构造滚动特征本身（只提供换算所需字段和资格计数）。
- 不写 LSE 代码路径；`market` 枚举预留 `LSE`。
- 不实现局部 upsert。

## Decisions

### D1. 演进而非重写
保留 `publish()` 的版本切换和 `security_grids()` 的逐证券流式结构，在网格层加入新列。**备选**：重写为宽表批处理——会丢失已验证的原子发布与内存控制，收益不大。

### D2. 事件日历是仓库内的参考数据
`scripts/data_pipeline/reference/index_events.csv` 纳入 Git（它不是下载数据，而是人工核验的规则输入），字段：`event_family, index, announce_date, execution_close_date, effective_date, markets, source_url, verified`。文件 SHA256 进入处理指纹。半日市不入此文件，由 `common.calendar` 在运行时得出。**备选**：放在 `Data/`——会被 Git 忽略，丢失版本追溯；运行时联网抓取——违反 `process` 离线要求。

首版由实施者按官方来源逐年核验 2022–2026 的日期，`verified=false` 的条目仍会剔除，但质量报告会列出。

### D3. 网格层的屏蔽语义
网格保留所有 XNYS 会话（现有做法，保证"前 10 个会话"是日历意义）。新增：
- `in_listing`：EXCHG∈{11,14} 生效区间内（原 `expected`）。
- `masked`：事件日或半日市。
- `expected = in_listing & ~masked & date ≤ common_end`。

窗口：`valid_day.shift(1).rolling(10, min_periods=1).sum()`，其中 masked 日的 `valid_day` 恒为假——数学上等价于"屏蔽后不向前补足"，且不需要第二套窗口逻辑。

### D4. 标签与匹配解耦
`match_status` 只描述身份（unique / ambiguous / missing_identity / unmatched），不再因场所冲突改为 ambiguous。新增：
- `label = xvol_n if market=='NYSE' else xvol_q`
- `label_status ∈ {ok, listing_venue_cross_missing, cross_outside_known_venues, no_taq_record}`
- `venue_reconciliation ∈ {consistent, venue_conflict, listing_cross_missing, identity_unresolved}`

`valid_day` 用 `label` 而非 `auction_volume`。

### D5. 普通股过滤位置
在网格构建前过滤 `tpci=='0'`，但被排除证券仍写入观察层的 `security_quality.csv`，`reason='non_common_stock:<tpci>'`，使漏斗可追溯。`tpci` 取该证券最常见值，若一只证券出现多个值则 `unresolved`（抽样未发现，但需防御）。

### D6. PIT 计数用移位累计和
每证券在网格内：`pit_n_expected = expected.cumsum().shift(1)`，`pit_n_anomaly = (expected & ~valid_day).cumsum().shift(1)`，`pit_eligible = pit_n_anomaly*20 ≤ pit_n_expected & prior_10_valid_days ≥ 6 & valid_day & match_status=='unique'`。O(n)，且下游按截止日 c 冻结时只需取 c 日的计数。`pit_n_expected==0` 时 `pit_eligible=False`。

### D7. 拆股：存因子，不存 (s,t) 对
所有 (s,t) 组合不可存。观察层每个证券日存 `ajexdi` 与 `split_segment_id`（遇 `split_unresolved` 断点递增）。下游换算 `vol_s × ajexdi_s / ajexdi_t` 只允许在同一 `split_segment_id` 内进行；提供 `splits.to_basis(vol_s, ajexdi_s, ajexdi_t)` 小函数与单元测试锁定方向。

一致性检查：`ajexdi` 变化日 r = ajexdi_{prev}/ajexdi_{curr}，p = prccd_{prev}/prccd_{curr}；`|p/r − 1| > 0.20` → 断点 + `split_unresolved`。跳变疑点：`ajexdi` 不变但 `|log(p)| > log(1.8)` 或 `cshoc` 比 > 1.8 或 < 1/1.8，只报告不断点。**备选**：只存"换算到样本末端"的量——这就是旧版的事后口径问题。

### D8. 检查点以 security_id 为键
检查点长表从已匹配网格（`match_status=='unique'`）关联生成，键 `(security_id, date, checkpoint)`；保留 `taq_symbol` 列。半日市行保留，收盘后检查点写 `not_applicable` 而非省略，使长表在每个会话上都形状一致。

### D9. 输出布局
沿用 `us_wrds_daily/`、`us_wrds_taq/`、`us_wrds_quality/`，新增：
- `us_wrds_quality/research_sample.csv.gz`（ex-post 合格 ∪ PIT 合格的股票日，带两套资格列）
- `us_wrds_taq/checkpoints_research.csv.gz`
- `reference/index_events.csv`（处理时副本）、`reference/field_dictionary.csv`（字段、单位、口径、`available_at`）
- `report/`：`daily_counts.csv` + `daily_counts.png`、`funnel.csv`、`label_reconciliation.csv`、`excluded_securities.csv`、`split_issues.csv`、`outside_venue_crosses.csv`、`diff_vs_previous.json`

图用 matplotlib，加入 `requirements-wrds.txt`。`VERSION` 升到 `wrds-v2`，仅影响处理指纹，不使 raw 分区失效——需要把 raw 元数据版本与处理规则版本拆为两个常量（`RAW_VERSION='wrds-v1'`、`PROCESS_VERSION='wrds-process-v2'`）。

**实施中的调整**：旧 `checkpoints_clean.csv.gz`（全部 TAQ 符号、约 9,500 万行、内存中 pivot）删除，由 `checkpoints_research` 取代；全部符号的检查点仍在 `daily_summary` 宽表中。`checkpoints_research` 的键由逐证券流式写出保证唯一，只做行数校验，不做全量键集合去重（约 3,600 万行）。

### D11. 窗口外证券
`sec_idhist` 含大量只在处理窗口之外上市的证券。它们标为 `outside_window`（不是 `unresolved`），在 `tpci` 判断之前过滤；只有"窗口内有 N/Q 区间但无日频记录"的证券才是 `unresolved`。

### D10. 与上一版的差异
发布前读取旧 `wrds_current` 的 `research_sample` 键集合，与新版比较，写 `diff_vs_previous.json`（新增 / 删除 / 资格改变股票日数）。首次运行时为空比较。

## Risks / Trade-offs

- [事件日期人工核验出错] → `verified` 字段 + 报告列出未核验条目；测试校验每条执行日是 XNYS 会话。
- [`tpci` 为快照，历史上类型变化的证券被误分类] → 清单注明；概率低，影响有限。
- [PIT 只到"t−1 已入库"的假设，实际 WRDS 有 1 日左右发布延迟] → 字段字典注明假设；若需更严格，下游可用 t−2 计数（数据已足够）。
- [网格行数增加（新列 + 检查点长表约 600 万股票日 × 6）] → 保持流式写出；检查点表仅对 `unique` 匹配生成。
- [`RAW_VERSION` 拆分写错会使 1,286 个 raw 分区被判失效并触发重下] → 单独测试：旧元数据 `wrds-v1` 读入时仍通过校验。
- [ET 类迁出证券的竞价打在未知场所] → 区间边界已处理研究样本；未知场所代码只影响审计，列为后续项。

## Migration Plan

1. 拆分版本常量并加测试，确认 `audit` 仍为 complete。
2. 实现新逻辑与测试，在小区间（如 2024-05-01 至 2024-07-31，含 NVDA 拆股、Russell 重构、7-03 半日市）运行 `process --data-dir` 指向临时副本核对。
3. 全量运行 `process`，首次发布 `wrds_current`。
4. 回滚：`wrds_current` 指回旧版目录（首次运行无旧版，删除生成目录即可）。

## Open Questions

- EXCHG `24` 对应的交易所与 TAQ `ex` 代码（需 WRDS 查询，不阻塞）。
- 竞价打印是否会早于收盘时间戳从而被 `continuous_volume` 重复计入（需 WRDS 抽查，不阻塞）。
- Russell 2026 起的 12 月重构具体日期以 FTSE Russell 官方公告为准。
