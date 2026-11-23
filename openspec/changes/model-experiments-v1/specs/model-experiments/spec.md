## ADDED Requirements

### Requirement: Explicit feature configuration
每个实验的模型输入 SHALL 只由 TOML 配置中 `sets` 展开的列构成。列名 MUST 存在于特征字典且 stage 与实验阶段一致，否则运行失败。系统 MUST NOT 自动纳入未列出的数值列。运行记录 SHALL 保存展开后的列清单及其哈希。

#### Scenario: Unlisted numeric column
- **WHEN** 特征表新增一个未在配置中列出的数值列
- **THEN** 所有模型预测不变

#### Scenario: Wrong stage
- **WHEN** 日频实验配置包含 `q_b`
- **THEN** 运行失败并指出该列

### Requirement: Two-stage information sets and baselines
日频模型 SHALL 预测 `y = log1p(V^A)`；盘中模型 g_b SHALL 以 `{q_b, yD_hat}` 学习 `y − yD_hat`，预测为 `yD_hat + g_b`，每个市场与检查点分别拟合。同一实验内线性与树模型 MUST 使用同一列表。基准 SHALL 固定为：`naive_last = a_last`、`ma22 = a_ma22`、`har` = 训练窗口 OLS(`a_last`,`a_ma5`,`a_ma22`,`dow`,`month_end`,`quarter_end`,`opex`)；盘中 B0（g=0）、B1（g=`q_b`）。NYSE 与 Nasdaq SHALL 分别建模。

#### Scenario: Same columns across model families
- **WHEN** 运行 D2
- **THEN** OLS、Ridge、Lasso、LightGBM、XGBoost、CatBoost 的展开列清单哈希相同

### Requirement: Identical evaluation samples
日频评估样本 SHALL 为 `research_included_pit` 且评估必需 6 列 `core_required` = {`a_last`, `a_ma5`, `a_ma22`, `d_last`, `d_ma22`, `logmcap_lag1`} 全部非缺失的行（该清单写在配置中，区别于 12 列 core 特征；与特征表的 `core_required_complete` 一致，样本只从特征表构造）；盘中另需 `C_b` 有效且 `mu_b` 非缺失。画像缺失或 `unassigned` MUST NOT 改变样本。系统 SHALL 按市场、期间、阶段、检查点记录样本哈希，比较双方哈希不同时 MUST 拒绝比较。

#### Scenario: D1 D2 D3 share rows
- **WHEN** 比较 D1、D2、D3 的验证结果
- **THEN** 三者样本哈希相同

### Requirement: Time splits and single test use
训练 T = [2022-04-01, 2025-01-01)，验证 V = [2025-01-01, 2025-07-01)，测试 S = [2025-07-01, 数据截止]。日频配置 SHALL 在 V 上选择，随后固定配置在 T∪V 上重拟合全部组件（预处理、聚类、模型），在 S 上只评估一次。S 的标签 MUST NOT 用于任何拟合或选择。

#### Scenario: Test labels isolated
- **WHEN** 扰动 S 期间的全部标签
- **THEN** 所有选择结果、artifact 与 S 上的预测不变，只有指标改变

### Requirement: D1 D2 D3 experiments and RMSE-tolerance selection
日频第一版 SHALL 包含 D1（core）、D2（core + `cluster_id`，K∈{3,5,8,10}）、D3（core + 与 D2 相同冻结 winsorize 的两项画像 `pf_lshare`、`pf_dev_std`，不聚类；`pf_dev_ac1` 只作诊断）。选择 SHALL 只依据验证集 log 空间 RMSE，容忍度 τ = 0.5% 预先固定，MUST NOT 依据测试结果修改，MUST NOT 使用 p 值。每个市场分别执行：(1) 每个（实验, 模型, K）取验证 RMSE 最低的超参数；(2) `R(K)` 为 D2 在 K 下各模型最低 RMSE，K* 为满足 `R(K) ≤ (1+τ)·min R` 的最小 K；(3) `R(E)` 为实验 E 各模型最低 RMSE（D2 用 K*），在满足 `R(E) ≤ (1+τ)·min R` 的实验中按 D1 → D3 → D2 顺序取第一个，记 E*；(4) E* 内 RMSE 最低的模型。保留结论 SHALL 直接由 E* 给出（D1：不采用画像与聚类；D3：保留画像、不采用聚类；D2：采用聚类）。全部实验、模型、K 的结果 SHALL 完整报告；显著性检验、置信区间、簇规模与稳定性只用于解释，"不显著" MUST NOT 写成"等效"。结论 MUST 注明市场、模型族与样本，MUST NOT 以簇的可解释性作为预测有效的依据；D2/D3 无增益时 SHALL 表述为"在该市场、模型族与样本下画像未提供额外预测价值"，MUST NOT 据此认定画像只是规模编码；`pf_dev_ac1` 的诊断 SHALL 表述为"自相关长期稳定性较低"。

#### Scenario: Smallest K within tolerance
- **WHEN** NYSE 验证 RMSE 为 K=3: 0.5030，K=5: 0.5010，K=8: 0.5000，K=10: 0.5004
- **THEN** K* = 5（0.5010 ≤ 0.5025，而 0.5030 > 0.5025）

#### Scenario: Experiment order within tolerance
- **WHEN** R(D1)=0.5020，R(D3)=0.5000，R(D2)=0.4990
- **THEN** 容忍上限 0.501495，候选为 D3、D2，按顺序选 D3，结论为"保留画像、不采用聚类"

#### Scenario: p-values ignored
- **WHEN** D2 与 D1 的 DM 检验 p=0.01 但 R(D1) ≤ 1.005·R(D2)
- **THEN** 选择 D1，报告中保留 DM 结果作解释

### Requirement: Rolling-origin selection and walk-forward predictions
锚点 SHALL 为 2023-07-01、2024-01-01、2024-07-01、2025-01-01、2025-07-01。每个锚点 A SHALL 只用 [2022-04-01, A−6 个月) 拟合、[A−6 个月, A) 打分，执行与官方选择相同的完整规则，得到配置 C_A。季度 Q（2023Q3–2025Q2）的 `yD_hat` SHALL 由 C_{A(Q)}（不晚于 Q 起点的最近锚点）在 [2022-04-01, Q 起点) 上重拟合预处理、聚类与模型后产生。任何折 MUST NOT 使用 Q 起点及以后的数据，包括设置禁区的情形。预算 SHALL 固定为：全部选择锚点预计墙钟时间 ≤ 12 小时，预计值 = 首锚点（两市场）实测时间 × Σ_k n_k / n_1（n_k 为锚点 k 内部训练行数）；超出时 SHALL 改用年度日程（锚点 2023-07-01、2024-07-01、2025-07-01；2023Q3–2024Q2 用 2023-07-01 配置，2024Q3–2025Q2 用 2024-07-01 配置）。该决定 SHALL 在首锚点之后、任何后续锚点之前写入 `selection/schedule.json`，只依据耗时，MUST NOT 读取任何锚点的选择结果。每条 `yD_hat` SHALL 记录 `anchor`、`config_id`、`experiment`、`model`、`K`、`fit_start`、`train_cutoff`、`fold` 与 `cluster_artifact_id`。

#### Scenario: Fallback decided by timing only
- **WHEN** 首锚点实测 3 小时且 Σ n_k / n_1 = 4.5
- **THEN** 预计 13.5 小时 > 12 小时，`schedule.json` 记录年度日程，后续只运行 2024-07-01 与 2025-07-01 锚点

#### Scenario: Upstream provenance recorded
- **WHEN** 读取 2024Q2 某行 `yD_hat`
- **THEN** 其 `anchor` = 2024-01-01（半年度日程），`train_cutoff` = 2024-04-01

#### Scenario: Fold isolation
- **WHEN** 扰动 2024Q2 起点及以后的全部数据
- **THEN** 2024Q1 及以前各折的配置、artifact 与 `yD_hat` 逐位不变

#### Scenario: Config chosen before fold
- **WHEN** 生成 2023Q4 的 `yD_hat`
- **THEN** 使用 2023-07-01 锚点的配置，该配置只依赖 2023-07-01 之前的数据

### Requirement: Intraday residual model schedule
g_b SHALL 在 2023Q3–2024Q4 的 walk-forward 残差上拟合，在 2025Q1–Q2 选择模型与超参数（I0-线性与 I0-LGBM 按同一 τ=0.5% 规则，接近时取线性），在 2023Q3–2025Q2 重拟合后冻结；在 S 上使用日频最终模型（T∪V 重拟合）的 `yD_hat`。g_b 的训练 MUST NOT 使用日频模型对其自身训练数据的拟合值。报告 SHALL 说明 g_b 学习的是逐锚点执行的历史模型选择流程所产生预测的残差（不同季度可来自不同配置），并按锚点分组列出残差统计。

#### Scenario: In-sample daily fit rejected
- **WHEN** g_b 训练集包含由覆盖该日期的日频拟合产生的 `yD_hat`
- **THEN** 运行失败

### Requirement: Strictness disclosure
报告 SHALL 区分由构造保证的时点约束（参数与配置只用预测季度前的数据）与未由构造保证的部分：候选空间与定义的设计、可得性假设 A1、`research_included_pit` 当日标签条件、数据快照、普通训练拟合中的样本内影响。报告 MUST NOT 将 walk-forward 结果描述为"完全严格样本外"。

#### Scenario: Report section
- **WHEN** 生成 `report.md`
- **THEN** 包含严格性边界一节，逐项列出上述内容

### Requirement: Incremental reporting
每个带父实验的实验 SHALL 输出：各模型的 ΔRMSE、ΔMAE、ΔR²_OOS（相对 MA22）及相对父实验的差、DM-NW 检验、按市值五分位、市场、簇、月末/普通日的分组差、新增特征的重要性（树：gain/SHAP；线性：标准化系数），以及样本一致性校验结果。

#### Scenario: D2 versus D1
- **WHEN** 生成增量表
- **THEN** 每个模型族一行，含 DM 统计量与 p 值

### Requirement: Reproducible runs
`run_id` SHALL 由解析后配置、特征指纹与 git 提交确定；工作区有未提交改动时 MUST 拒绝正式运行。运行目录 SHALL 保存配置、包版本、种子、样本哈希、聚类与预处理 artifact、各锚点选择记录、预测、指标与报告。相同 `run_id` 重跑 MUST 得到逐位相同的 artifact 与预测。

#### Scenario: Dirty worktree
- **WHEN** 存在未提交修改时执行正式运行
- **THEN** 运行拒绝并提示提交
