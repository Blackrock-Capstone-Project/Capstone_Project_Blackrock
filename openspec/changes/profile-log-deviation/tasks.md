## 1. 实现

- [x] 1.1 `profiles.py`：计算 `la_ma22`（与 `a_ma22` 同一可用槽位集合上的 ln 均值），`e_s = ln(V^A_s) − la_ma22_s`；`config.FEATURE_VERSION = 'features-v2'`
- [x] 1.2 字典定义更新；修正 `auction_features` 文档字符串中失效的规格路径

## 2. 测试

- [x] 2.1 参考实现改为 ln 口径；对拍、未来扰动、配对规则测试通过
- [x] 2.2 一致拆股：四项画像在 `1e-12` 容差内相同
- [x] 2.3 daily 与 intraday 表在 v1/v2 代码下内容哈希相同（真实数据，诊断脚本内核对）

## 3. 诊断（提交前，临时目录，不发布）

- [x] 3.1 在真实数据上计算 v2 画像：相关矩阵（含 `pf_dollar_auc`）、6 个月重测相关、覆盖与状态分布、稳健/普通版本对比
- [x] 3.2 与 v1 对比，并统计窗口含拆股的画像受影响程度；向用户汇报

## 4. 提交与正式版本（用户确认后）

- [x] 4.1 更新 `docs/FEATURES.md`、`docs/OPEN_ITEMS.md`；提交代码
- [ ] 4.2 提交后构建正式 features-v2 版本并切换 `current`；v1 版本目录保留；verify 通过
