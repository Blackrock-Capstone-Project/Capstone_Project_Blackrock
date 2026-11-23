## 1. 骨架与依赖

- [x] 1.1 `requirements.txt` 增加固定版本 `pyarrow`；建立 `auction_features/{__init__,__main__,config}.py`
- [x] 1.2 `dictionary.py` 规格表（name/table/stage/group/定义/来源/窗口/可用条件/时点/A1；元数据与 ex-post 字段）

## 2. 输入固定与历史面板

- [x] 2.1 `panel.py`：解析 `wrds_current` 为版本目录（或 `--processed-dir`）、按 processed manifest 校验输入 sha256、结束时复核
- [x] 2.2 `panel.py`：读 `stock_day_quality` + `daily_clean` 价格/股本 + 检查点；U_A/U_V/U_P/U_C、连续市场区间、`seg_ref`、`f_t`/`factor_carried`/`factor_unknown`、`split_on_t`、`expost_split_issue_t`
- [x] 2.3 `windows.py`：会话窗口（屏蔽与未上市占位）、严格 lag1、last/age、按 `f_t` 换算、历史槽位缺失因子不可用
- [x] 2.4 测试：窗口不压缩、屏蔽占位、个股隔离、转板/回转/重新上市断点、`seg_ref` 断点、2:1 拆股、t 日异常仅诊断、缺失因子沿用与未知、缺失股本不沿用、min_periods 边界、不以 0 填充

## 3. 特征、元数据与画像

- [x] 3.1 `daily.py`：核心 12 列、诊断列、`y`、透传元数据、派生标志、`core_required_complete`
- [x] 3.2 `intraday.py`：`C_b`、`mu_b`、`q_b`
- [x] 3.3 `profiles.py`：三项行为画像（区间内观测）、`pf_dollar_auc`、`profile_status`、诊断列
- [x] 3.4 测试：同日独立、未来扰动（特征与画像，逐位相等）、研究标志无关、元数据逐值等于输入、画像配对规则、新股缺失、退化离散度、一致拆股下画像不变（浮点容差）
- [x] 3.5 逐行参考实现（测试内）与向量化对拍：NaN 位置相同、整数类逐值相等、浮点 `|Δ| ≤ 1e-12 + 1e-12·|ref|`

## 4. 发布、报告与 CLI

- [x] 4.1 `build.py`：指纹、复用（不写文件）、临时目录写入、校验、改名、`current` 原子切换、内容哈希、manifest
- [x] 4.2 `report.py`：缺失、历史长度、异常值、覆盖、核心完整率与原因分解（99.5% 复核触发，不失败）、拆股/因子/市值跳变诊断、画像覆盖与重测相关
- [x] 4.3 CLI `build|report|verify`：`verify` 只读，`report` 写 `Data/features/reports/<id>/`；`notebooks/build_features.ipynb` 只调包内函数
- [x] 4.4 测试：字典一一对应、主键唯一、行集合、两次构建内容哈希相同、复用不改文件、report 不改版本目录、失败构建不切换 `current`

## 5. 真实数据验收与文档

- [x] 5.1 全量构建；processed 指纹与输入 sha256 前后相同；原有 48 项测试通过
- [x] 5.2 抽 50 只证券（含拆股、转板、重新上市样例）：截断面板 vs 全面板逐位相等；参考实现对拍满足容差
- [x] 5.3 复用验证：同输入再次 build 返回原版本且目录未改动；新目录从头重建内容哈希一致
- [x] 5.4 质量报告：核心完整率与原因、画像早期缺失比例、拆股与因子诊断；在验收报告中说明是否触发复核
- [x] 5.5 `docs/FEATURES.md`（定义表由字典生成）、README 与 `docs/OPEN_ITEMS.md`（A1 假设、股本滞后）更新
