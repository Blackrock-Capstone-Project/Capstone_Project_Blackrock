## Why

`wrds-process-v3` 已产出标签、屏蔽日、PIT 标志与拆股标注，但没有任何预测特征；拆股只标注未换算，`research_sample` 按纳入标志压缩了日期，不能直接滚动。两阶段模型（日频 f、盘中 g_b）与 K-means 行为聚类需要一份只用历史、不做任何拟合、与样本筛选无关的特征面板。

## What Changes

- 新增 `auction_features` 包（`python -m auction_features build|report|verify`）。构建开始时把 `wrds_current` 解析为固定的版本目录并按 manifest 校验输入摘要，只读该目录；不修改 `auction_data` 及其产物。
- 在 `stock_day_quality` 全面板（每只证券的全部上市会话）上计算历史特征；屏蔽日与未上市会话占位；按观测族定义可用条件（成交量族沿用屏蔽，价格族可用屏蔽日）；竞价、成交量、检查点族的历史不跨连续市场区间（转板、回转或重新上市即断开）。
- 预测时拆股规则：历史只在 t−1 及以前最后已知分段内使用，按 `f_t` 换算（可得性假设 A1）；缺失因子沿用或置缺失并标记；市值只用 t−1 同日价格与股本、不沿用；t 日自身的拆股异常只作事后诊断列。
- 第一版只实现：日频核心 12 列（`*_last`/`*_last_age` 与严格 `*_lag1` 分开命名）、盘中核心（`C_b`、`mu_b`、`q_b`）、月初行为画像 3 项（`pf_lshare`、`pf_dev_std`、`pf_dev_ac1`）及可选规模画像 `pf_dollar_auc`（仅计算，默认不用）。扩展特征族不实现。
- daily 表自带目标与筛选元数据（逐值透传 processed，事后字段标注 ex-post），模型层只读特征表即可构造样本。
- 版本化 parquet 输出（`Data/features/current`）：临时目录写入后改名发布，已发布版本不再被改写（`report` 写到独立目录）；特征字典、manifest 与质量报告。
- 验收：不变性测试逐位相等；与参考实现对拍按浮点容差；核心完整率只作复核触发，不设硬门槛。
- 新增依赖 `pyarrow`（固定版本）。

## Capabilities

### New Capabilities
- `feature-panel`: 时点一致的日频、盘中、月度画像基础特征面板，包含可用条件、拆股换算、命名规则、字典、版本与质量报告。

### Modified Capabilities
无。`auction-research-dataset` 的要求不变；特征层只读其产物。

## Impact

新增 `auction_features/`、`tests/test_features_*.py`、`notebooks/build_features.ipynb`、`docs/FEATURES.md`；`requirements.txt` 增加 `pyarrow`；`Data/features/`（Git 忽略）。`auction_data/` 与 `Data/processed/` 不变。后续 `model-experiments-v1` 只读本变更的输出。
