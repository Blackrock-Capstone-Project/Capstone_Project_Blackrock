## 1. 版本常量与基线

- [x] 1.1 在 `common.py` 拆分 `RAW_VERSION='wrds-v1'`（raw 元数据、`query_version`、plan config）与 `PROCESS_VERSION='wrds-process-v2'`（处理指纹、manifest），替换 `common/sources/pipeline/processing` 中的 `VERSION` 引用
- [x] 1.2 测试：已有 `wrds-v1` raw 元数据仍通过 `read_partition` 校验；`audit_data()` 对现有 `Data/` 返回 complete（1,286 分区）
- [x] 1.3 运行现有测试套件，确认拆分后全部通过

## 2. 事件日历参考数据

- [x] 2.1 新建 `scripts/data_pipeline/reference/index_events.csv`（D2 字段），按官方来源逐年登记 2022–2026 的 S&P 季度、Nasdaq-100 年度、Russell（含 2026 起 12 月）、MSCI 季度/半年度执行收盘日，填 `source_url` 与 `verified`
- [x] 2.2 新建 `events.py`：加载清单、校验字段与日期均为 XNYS 会话、按市场日期去重得到 `event_days`；从 `common.calendar` 得到半日市及其收盘时间
- [x] 2.3 测试：同日多事件只剔除一次；非会话日期报错；2025-11-28 识别为 13:00 收盘；清单 SHA256 进入处理指纹

## 3. 普通股范围与标签

- [x] 3.1 在网格构建前过滤 `tpci=='0'`，被排除证券写入 `security_quality.csv`，`reason='non_common_stock:<tpci>'`；一只证券多个 `tpci` 值时标 `unresolved`
- [x] 3.2 观察层加入 `is_reit`、`is_spac`（NA）及 `tpci` 快照说明
- [x] 3.3 `security_grids` 合并 TAQ 时带入 `xvol_n/q/p/a/z`、`auction_volume_all`；新增 `label`、`label_status`、`venue_reconciliation`（D4）；`match_status` 不再因场所冲突改为 ambiguous
- [x] 3.4 `score_grid` 的 `valid_day` 改用 `label`
- [x] 3.5 测试：BRK.B↔BRK/B 匹配；`tpci='R'` 排除；NYSE 证券 `xvol_n` 空而 `xvol_p` 有值 → 标签 NA 且 venue_conflict；`auction_volume_all>0` 且 xvol 全空 → `cross_outside_known_venues`；OBK 2023-05-22 前后分别取 `xvol_q`/`xvol_n` 且窗口连续

## 4. 网格屏蔽与 ex-post 资格

- [x] 4.1 网格新增 `in_listing`、`masked`（事件日或半日市）、`expected = in_listing & ~masked & date ≤ common_end`（D3）；masked 日 `valid_day` 恒为假
- [x] 4.2 5% 分母与分子只在 `expected` 上计算；`unresolved` 条件保持
- [x] 4.3 `exclusion_reason` 增加 `event_day`、`half_day`，并按漏斗固定顺序取第一个命中原因
- [x] 4.4 测试：恰好 5% 保留 / 6% 剔除；窗口内 1 个事件日 + 6 有效 → 合格；屏蔽后 5 有效 → 不合格；ET 2026-10-05 起不在 `in_listing`，不计异常；来源缺分区不计异常、完整分区内个股缺记录计异常

## 5. PIT 资格

- [x] 5.1 实现 `pit_n_expected`、`pit_n_anomaly`（移位累计和，D6）与 `pit_eligible`
- [x] 5.2 测试：修改 t+30 之后的数据不改变 t 日 PIT 字段；新股第 12 会话有 1 个异常 → `pit_eligible=False`；`pit_n_expected==0` → False

## 6. 拆股换算

- [x] 6.1 新建 `splits.py`：`to_basis(vol_s, ajexdi_s, ajexdi_t)`；逐证券检测 `ajexdi` 变化、价格比一致性（20%）、`split_segment_id` 与 `split_unresolved`；跳变疑点（1.8 倍阈值，只报告）
- [x] 6.2 观察层输出 `ajexdi`、`split_segment_id`、`split_status`
- [x] 6.3 测试：NVDA 2024-06-07 40.9M → 2024-06-12 口径 409M；全部 `ajexdi` 同乘常数结果不变；因子比 10 而价格比 3 → unresolved；用真实数据验证 NVDA、AVGO、WMT、CMG 拆股日期与方向，并从数据中检索 ≥2 个合股样例写入测试夹具

## 7. 检查点长表与字段字典

- [x] 7.1 由 `match_status=='unique'` 的网格生成 `checkpoints_research.csv.gz`，键 `(security_id, date, checkpoint)`，保留 `taq_symbol`；半日市收盘后检查点写 `not_applicable`
- [x] 7.2 保留小数、非有限/负值/倒退标记；以当日最终量为分母的比例列加 `_diag` 后缀
- [x] 7.3 生成 `reference/field_dictionary.csv`：字段、来源、单位、聚合口径（`tr_corr='00'`、竞价窗口 −1/+5 分钟、条件码 `6`、`auction_price` 取 MAX、TRF 30 分钟同价）、`available_at`
- [x] 7.4 测试：半日市只有 10:00、12:00 为 applicable；字典中竞价量、`continuous_volume`、`trf_close_volume`、`cshtrd` 为 `t post-close`

## 8. 发布、报告与差异

- [x] 8.1 `publish()` 写出 `research_sample.csv.gz`、检查点表、`reference/` 副本，全部读盘校验主键与行数后进入 manifest
- [x] 8.2 生成 `report/`：`daily_counts.csv` + `daily_counts.png`（区分保留、事件日、半日市、来源缺口）、`funnel.csv`、`excluded_securities.csv`、`label_reconciliation.csv`、`outside_venue_crosses.csv`、`split_issues.csv`、未核验事件条目、PIT 与 ex-post 不一致比例；`requirements-wrds.txt` 加入 matplotlib（固定版本）
- [x] 8.3 `diff_vs_previous.json`：与旧 `wrds_current` 的研究样本比较新增 / 删除 / 资格改变股票日数
- [x] 8.4 测试：同一输入重复运行指纹一致且复用；漏斗中事件日 + 当日异常只计一次；失败时 `wrds_current` 不切换

## 9. 运行与文档

- [x] 9.1 在 `Data/` 临时副本上以 2024-05-01 至 2024-07-31 小区间运行 `process`，人工核对 NVDA、Russell 重构日、2024-07-03 半日市
- [x] 9.2 全量运行 `process`，检查质量报告并记录关键数字（证券数、股票日数、剔除名单规模、对账四类计数）
- [x] 9.3 更新 `docs/WRDS_DATA_PIPELINE.md` 与 `README.md` 的输出契约、标签定义、事件日、PIT 与拆股说明；在文档中列出后续项（REIT/SPAC 行业表、EXCHG `24` 场所代码、竞价与连续量重叠核验）
