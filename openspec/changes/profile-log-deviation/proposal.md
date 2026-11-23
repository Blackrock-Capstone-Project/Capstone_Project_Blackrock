## Why

features-v1 的画像偏离 `e_s = ln(1+V^A_s) − a_ma22_s` 两侧都用 log1p，以 s 日股数为口径时 +1 项使 `pf_dev_std`、`pf_dev_ac1` 在一致拆股前后只近似不变（偏差约 `1/V^A`）；真实 pit 行竞价量 1% 分位只有 20 股，低量股票窗口含拆股时偏差不可忽略。改为两侧都用 ln，使画像对一致拆股精确不变（浮点舍入内）。

## What Changes

- 画像偏离改为 `e_s = ln(V^A_s) − la_ma22_s`，其中 `la_ma22_s` 是与 `a_ma22_s` 完全相同的可用槽位集合（U_A、同一连续市场区间、`seg_ref_s`、最少 15）上 `ln(V^A_r·f_r/f_s)` 的均值；两侧一起改。`la_ma22_s` 只在画像内部使用，不进入 daily 表。
- `pf_dev_std`、`pf_dev_ac1` 及其诊断列随之重算；`pf_lshare`、`pf_dollar_auc`、窗口、阈值、配对与截断规则不变。
- 日频目标 `y = log1p(V^A)` 与日频核心特征（`a_*` 用 log1p、`d_*` 用 log）定义不变。
- `FEATURE_VERSION` 升为 `features-v2`；已验收的 features-v1 版本（`2a44b113f72499b4`）保留，不覆盖。
- 修正 `auction_features` 文档字符串中归档后已失效的规格路径。

## Capabilities

### New Capabilities

无。

### Modified Capabilities
- `feature-panel`: "Monthly behaviour profiles" 中 `e_s` 的定义与一致拆股场景改为 ln 口径、精确不变。

## Impact

`auction_features/{profiles,config,dictionary,__init__}.py`、`tests/feature_helpers.py`、`tests/test_features_panel.py`、`docs/FEATURES.md`、`docs/OPEN_ITEMS.md`。新的正式特征版本在本变更代码提交后构建；诊断在提交前于临时目录计算，不发布到 `Data/features/`。
