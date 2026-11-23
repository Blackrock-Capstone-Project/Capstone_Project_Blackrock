## 1. 配置与数据

- [ ] 1.1 `requirements.txt` 固定 scikit-learn、lightgbm、xgboost、catboost 版本；建立 `auction_models/` 与 `experiments/daily_v1.toml`、`intraday_v1.toml`（候选空间、网格、锚点、回退方案在首次运行前提交）
- [ ] 1.2 `experiment.py`：解析、字典与 stage 校验、列清单哈希、run_id、未提交改动拒绝
- [ ] 1.3 `data.py`：评估样本、样本哈希、切分、锚点与季度折
- [ ] 1.4 测试：未列出数值列不影响预测、stage 错误报错、D1/D2/D3 样本哈希相同

## 2. 预处理与聚类

- [ ] 2.1 `preprocess.py`：窗口内拟合的 winsorize、标准化、中位数填补与缺失指示、one-hot
- [ ] 2.2 `cluster.py`：拟合样本、预处理、K-means、编号、artifact、月度指派、`unassigned`
- [ ] 2.3 测试：窗口外扰动不改参数、月内归属不变、转板与新股 unassigned、类别全集固定、重复拟合一致

## 3. 模型与流水线

- [ ] 3.1 `models.py`：naive_last、MA22、HAR 与六类模型统一接口（类别编码按模型族）
- [ ] 3.2 `pipeline.py`：`fit_daily(config, 窗口)` 为唯一拟合入口
- [ ] 3.3 `selection.py`：超参数、K（τ=0.5%，最小 K）、实验（τ，D1→D3→D2）、模型规则；DM-NW 仅报告；每次选择写入 `selection/<anchor>.json`
- [ ] 3.4 测试：测试期标签扰动不改变选择与预测；同实验各模型列一致

## 4. 滚动原点与盘中

- [ ] 4.1 `stage2.py`：锚点选择、walk-forward 折、`yD_hat` 生成；记录首锚点耗时，按 12 小时预算公式写 `selection/schedule.json` 并决定是否回退年度日程；每条 `yD_hat` 记录 anchor、config_id、train_cutoff 等
- [ ] 4.2 g_b：B0、B1、I0（线性、LGBM），按市场与检查点；训练/选择/最终时序
- [ ] 4.3 测试：折隔离（扰动 Q 起点后数据不改前折）、配置来自不晚于 Q 的锚点、拒绝样本内 `yD_hat`

## 5. 评估与报告

- [ ] 5.1 `evaluate.py`：指标、DM-NW、分组、增量表、重要性
- [ ] 5.2 聚类诊断：规模、中心、转移率、种子 ARI、轮廓、unassigned、相关矩阵、η² 与市值交叉表、重测可靠性
- [ ] 5.3 `report.md` 含严格性边界一节
- [ ] 5.4 相同 run_id 重跑逐位一致

## 6. 正式运行与文档

- [ ] 6.1 提交配置后运行日频选择（含锚点）、日频最终、walk-forward 与 g_b
- [ ] 6.2 测试集一次评估；按市场、模型族、样本表述 D1/D2/D3 结论
- [ ] 6.3 同步 `MODEL_FRAMEWORK.md`、`docs/KMEANS_RESEARCH_AND_DESIGN_BRIEF.md`、README
