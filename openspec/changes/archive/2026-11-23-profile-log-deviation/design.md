## Context

features-v1 已验收并提交（`5998ecc`），当前版本 `Data/features/.versions/2a44b113f72499b4`（manifest 记录 git dirty=true，代码摘要与提交一致）。实测：一致拆股合成样例中 `pf_dev_std`、`pf_dev_ac1` 的偏差约 1e-6（V^A≈4×10⁴），偏差量级 `1/V^A`。

## Goals / Non-Goals

**Goals:** 画像偏离对一致拆股精确不变；其余画像、日频目标与核心特征不变；保留 v1 版本与其 provenance。

**Non-Goals:** 收缩或其他额外处理；画像输入取舍（由诊断结果再定，属 model-experiments-v1）；模型实验。

## Decisions

- `e_s = ln(V^A_s) − la_ma22_s`，`la_ma22_s = mean_{r∈R_s} ln(V^A_r · f_r / f_s)`，`R_s` 与 `a_ma22_s` 的可用槽位集合相同（W(s,22) 内 U_A、同一连续市场区间、`seg_r = seg_ref_s`、`f_r` 已知），`|R_s| ≥ 15`。故 `e_s` 有值当且仅当 `a_ma22_s` 有值，`n_e`、`n_p` 的计数规则不变。
- 由于 `V^A_s > 0`（U_A 要求），ln 有定义。两侧同为 s 日股数口径，一致拆股使两侧同时加上同一常数，差值不变；实现中 `ln(V/2)` 与 `ln V − ln 2` 只在浮点舍入上不同，测试容差 `1e-12 + 1e-12·|x|`。
- 选择 ln 而不保留 log1p 的理由：画像衡量的是"相对自身常态的比例偏离"，比例在 ln 下与股数单位无关；日频目标沿用 log1p 是为了与框架和基准一致，二者用途不同。
- 版本：`FEATURE_VERSION = 'features-v2'`；代码与配置变化使指纹改变，自动生成新版本目录，v1 目录不动。

## Risks / Trade-offs

- [ln 对极小竞价量（如 1–20 股）比 log1p 更敏感，`e_s` 离散度可能上升] → 稳健尺度（MAD）与 4 倍截断已在规则内；诊断报告比较 v1 与 v2 的离散度、相关与重测稳定性。
- [v1 与 v2 画像数值不同] → 两个版本并存，模型层只使用由 model-experiments-v1 确定的版本与输入。

## Migration Plan

提交前：在临时目录计算 v2 画像诊断并与 v1 比较，不发布。用户确认后提交代码，再构建正式 features-v2 版本并切换 `current`；v1 版本目录保留。
