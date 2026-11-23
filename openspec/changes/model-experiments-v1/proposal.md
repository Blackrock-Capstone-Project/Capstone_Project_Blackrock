## Why

`feature-panel` 提供无拟合的基础特征后，需要一个可复现的实验层来回答：日频核心特征相对基准的增益；股票行为画像是否有预测价值、以聚类标签还是连续画像形式；盘中检查点信息对日频预测的修正。K-means 是待验证的研究方法，必须与"直接提供同一画像"对照，并防止配置选择把未来信息带入盘中训练用的日频预测。

## What Changes

- 新增 `auction_models` 包与 `experiments/*.toml`：所有模型只使用配置中显式列出的特征。
- NYSE、Nasdaq 分别建模、分别拟合 K-means；日频 f 预测 `y = log1p(V^A)`；盘中 g_b 以 `{q_b, yD_hat}` 学习 `y − yD_hat`，预测 `yD_hat + g_b`。
- 日频第一版实验：基准（naive_last、MA22、HAR）、D1 核心、D2 核心＋`cluster_id`（K∈{3,5,8,10}）、D3 核心＋同两项连续画像（`pf_lshare`、`pf_dev_std`；`pf_dev_ac1` 只作诊断）；同切分、同评估样本。规模画像扩展后续再做。
- 聚类：训练窗口拟合 winsorize、标准化与簇中心并冻结；每月初用此前历史画像向冻结中心指派，月内固定；`unassigned` 为显式类别。
- 滚动原点选择：半年度锚点（预先固定 12 小时预算与年度回退日程）各自只用锚点前数据完成整套配置选择；walk-forward 每季度用最近锚点的配置、只用过去数据重拟合预处理、聚类与模型，生成训练 g_b 用的 `yD_hat`。
- 预注册的选择规则：只按验证 RMSE，固定容忍度 0.5%，K 取容忍范围内最小值，实验按 D1→D3→D2 取第一个（保留即选择）；p 值只作解释；最终在 T∪V 重训，测试集只评估一次。
- 运行记录：run_id、特征指纹、git 提交、评估样本哈希、聚类与预处理 artifact、增量报告与严格性披露。

## Capabilities

### New Capabilities
- `behaviour-clustering`: 画像预处理与 K-means 的拟合范围、冻结、月度指派、`unassigned` 规则、artifact 与诊断。
- `model-experiments`: 显式特征配置、两阶段信息集与基准、评估样本、时间切分、D1/D2/D3 与保留规则、滚动原点选择与 walk-forward、盘中残差模型、报告与复现。

### Modified Capabilities
无。

## Impact

新增 `auction_models/`、`experiments/daily_v1.toml`、`experiments/intraday_v1.toml`、`tests/test_models_*.py`、`Data/experiments/`（Git 忽略）；依赖已安装的 scikit-learn、lightgbm、xgboost、catboost（需写入 `requirements.txt` 并固定版本）。依赖 `feature-panel` 先完成。`MODEL_FRAMEWORK.md` 与 `docs/KMEANS_RESEARCH_AND_DESIGN_BRIEF.md` 同步。
