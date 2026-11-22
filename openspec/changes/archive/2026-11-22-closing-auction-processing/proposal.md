## Why

`wrds-data-pipeline` 已把 2022-01-03 至 2026-11-19 的 NYSE/Nasdaq 日频与 TAQ 汇总完整下载，但处理层仍未生成，且现有处理口径不足以直接支撑收盘竞价预测：标签取"最大竞价场所"而非上市交易所，股票池未限定普通股，未剔除指数再平衡日与半日市，历史量未统一拆股口径，资格判定只有事后全历史版本。研究阶段需要一套口径明确、可审计、可区分预测时点可见性的研究样本。

## What Changes

- 研究股票池限定为 Compustat `tpci='0'` 普通股；ETF、优先股、权证、units、封闭式基金、ADR 与债券型证券（`tpci='R'`）排除。REIT/SPAC 随普通股纳入，标记列为 NA 并列为后续项。
- **BREAKING**：竞价标签改为当日历史上市交易所的收盘竞价量（EXCHG=11 取 `xvol_n`，EXCHG=14 取 `xvol_q`），缺失即为标签缺失，不回退其他场所。原 `auction_volume`/`listing_ex` 保留用于对账。
- 新增版本化重大指数再平衡执行日清单（S&P 季度、Nasdaq-100 年度、Russell、MSCI），与半日市一起退出主样本、滚动窗口与 5% 分母；窗口屏蔽后不向前补足。
- 新增 PIT 资格字段：只用 t−1 及以前数据计算扩展窗口异常率，并输出累计计数以便后续按任意截止日冻结股票池；保留原事后（ex-post）资格。
- 新增拆股换算：用 `ajexdi_s / ajexdi_t` 比值把历史原生股数换算到预测日股本口径；因子与价格跳变不一致时标为 `split_unresolved`。
- 日内检查点长表改为以 `security_id` 为键，标注各字段可用时点（t−1 收盘 / t 日内 / t 收盘后）。
- 质量报告扩充：筛选漏斗、标签对账四类、迁出至非目标交易所的证券、拆股未决与跳变疑点、事件日覆盖、与上一版差异。

## Capabilities

### New Capabilities

- `auction-research-dataset`: 从已验证 WRDS raw 生成美股收盘竞价研究样本——普通股范围、上市场所标签、事件日与半日市剔除、事后与 PIT 资格、拆股换算、日内检查点可用性及质量报告。

### Modified Capabilities

- `wrds-data-pipeline`：以 MODIFIED 方式更新 "Volume validity and expected security sessions"（上市场所标签、分母扣除屏蔽日）、"Prior ten session cross-sectional eligibility"（屏蔽日占位不补足）、"Full history security exclusion"（分母、unresolved 暂缓）。下载、认证、分区校验与隐私要求不变。（归档前补充；`wrds-data-pipeline` 须先归档。）

## Impact

- 代码：`scripts/data_pipeline/quality.py`、`processing.py`、`pipeline.py`（`_process` 传入事件日历与规则版本）、新增事件日历与拆股模块、`tests/test_pipeline.py`；`taq_sql.py` 不改（不触发重新下载）。
- 数据：`Data/processed/` 产物 schema 变化（新列、新表、`security_id` 键的检查点表），处理规则版本号递增，全区间重建。
- 文档：`docs/WRDS_DATA_PIPELINE.md`、`README.md` 的输出契约与质量规则。
- 不涉及：模型训练、下载 SQL、LSE、新增 WRDS 表下载。
