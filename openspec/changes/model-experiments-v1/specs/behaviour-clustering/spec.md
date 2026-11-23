## ADDED Requirements

### Requirement: Window-scoped profile preprocessing and K-means fit
聚类 SHALL 每个市场独立拟合，输入为 features-v2 的两项行为画像 `pf_lshare`、`pf_dev_std`；`pf_dev_ac1` SHALL 只作诊断，MUST NOT 进入聚类。对拟合窗口 `[A,B)`，样本 SHALL 为 `m ∈ [A,B)`、`profile_status='ok'` 且该月至少一行 `research_included_pit` 的证券-月。winsorize 界限（1%/99%）、z-score 参数与簇中心 MUST 只由该样本拟合。K-means SHALL 使用 k-means++、`n_init=20`、固定种子；簇编号按中心 `pf_lshare` 升序。`pf_dollar_auc` MUST NOT 进入默认画像。

#### Scenario: Data after window end
- **WHEN** 扰动 B 及以后的全部画像与标签
- **THEN** 该窗口的 winsorize 界限、标准化参数、中心与编号逐位不变

#### Scenario: Markets fitted separately
- **WHEN** 同时拟合 NYSE 与 Nasdaq
- **THEN** 产生两份独立 artifact，簇编号不要求跨市场对应

### Requirement: Monthly assignment to frozen centroids
任意行 t 的 `cluster_id` SHALL 由 `month(t)` 月初画像（只用该月之前历史）经冻结的 winsorize 与标准化后指派到最近中心，同一月内不变。更新归属 MUST NOT 重新拟合任何参数。

#### Scenario: Constant within month
- **WHEN** 查询同一证券同一月的不同交易日
- **THEN** `cluster_id` 相同

#### Scenario: Validation rows use frozen parameters
- **WHEN** 为验证期行指派
- **THEN** 使用训练窗口 artifact，验证期数据不影响任何参数

### Requirement: Unassigned category
画像缺失（历史不足、区间断开、退化离散度）或行所在连续市场区间与画像所用区间不同（月中转板、重新上市）的交易日 SHALL 得到 `cluster_id='unassigned'`，作为第 K+1 个类别。`unassigned` MUST NOT 改变评估样本，MUST NOT 被填补为某个簇。

#### Scenario: New listing
- **WHEN** 证券月初画像为 NaN
- **THEN** 当月 `cluster_id='unassigned'`，该证券日仍在 D1/D2/D3 评估样本中

#### Scenario: Mid-month transfer
- **WHEN** 证券在月中由 NASDAQ 转至 NYSE
- **THEN** 转板日起至月末为 `unassigned`，下月按新区间画像与 NYSE artifact 重新评估

### Requirement: Categorical encoding
`cluster_id` SHALL 作为类别使用：线性模型 one-hot，参照组为训练窗口最大簇；树模型使用原生类别类型。类别全集 SHALL 由 artifact 固定（K 个簇加 `unassigned`），MUST NOT 按编号数值解释。

#### Scenario: Category absent in training rows
- **WHEN** 某簇在训练行中未出现而在验证行出现
- **THEN** 编码列集合不变，不报错

### Requirement: Clustering artifact
每次拟合 SHALL 保存 artifact：市场、拟合窗口、画像规格版本、证券-月数、winsorize 界限、标准化参数、K、种子、n_init、中心（标准化前后）、编号规则、inertia、拟合数据哈希与特征指纹，以及 证券 × 月 的画像、`cluster_id` 与状态表。

#### Scenario: Reproducible fit
- **WHEN** 以相同配置、特征版本与种子重复拟合
- **THEN** artifact 与指派表逐位一致

### Requirement: Clustering diagnostics
系统 SHALL 报告各簇规模与中心、月度转移率、5 个种子的 ARI、轮廓系数（抽样）、每月每市场 `unassigned` 比例及原因、标准化画像相关矩阵与特征值、`logmcap` 对簇的 η²、簇与市值五分位交叉表与 ARI、分簇误差。诊断 MUST NOT 参与 K 或配置选择。

#### Scenario: Size dominance check
- **WHEN** 生成诊断
- **THEN** 输出簇与市值五分位的交叉表及 η²
