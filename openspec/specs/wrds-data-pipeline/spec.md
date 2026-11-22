# wrds-data-pipeline Specification

## Purpose
从 WRDS（Compustat 日频与历史标识、TAQ 服务器端汇总）可重复地下载、校验、版本化发布 NYSE/Nasdaq 数据，并给出逐日有效性、10 日 6 有效与全历史 5% 质量判定；运行时认证且不保存凭证。研究样本口径见 `auction-research-dataset`。
## Requirements
### Requirement: Unified WRDS update entry
系统 SHALL 提供可导入的 Python 更新方法和调用相同逻辑的 CLI，默认从 2022-01-03 维护 NYSE/Nasdaq 历史，支持指定结束日期和市场，并返回逐来源覆盖、输出及完成状态。

#### Scenario: First update
- **WHEN** 用户在无本地数据的目录调用默认更新
- **THEN** 系统从 2022 年首个交易日起下载各来源实际可用的历史，执行校验和处理并返回报告

#### Scenario: Explicit interval
- **WHEN** 用户指定截止日期和 NYSE 范围
- **THEN** 系统采用该请求范围和历史市场身份，不使用当前 Bloomberg 股票池筛选

### Requirement: Runtime authentication without personal defaults
系统 MUST 使用用户运行时提供的认证信息或显式注入的连接；默认交互询问用户名、隐藏密码并配合必要登录步骤，不含个人账号默认值，不自动保存凭证。认证失败或取消 MUST 清晰终止，非交互无认证 MUST 快速失败。

#### Scenario: Interactive connection
- **WHEN** 用户未提供连接或认证提供器，在交互终端运行更新
- **THEN** 系统询问用户自己的账号并隐藏密码，允许用户完成所需登录步骤，不将认证信息持久化

#### Scenario: Missing noninteractive credentials
- **WHEN** 无交互终端且未提供认证
- **THEN** 系统返回明确认证错误，不无限等待输入

#### Scenario: Sensitive exception
- **WHEN** 底层连接异常包含用户名、密码或 DSN
- **THEN** 用户可见日志和持久化报告不包含这些敏感值

### Requirement: WRDS-only sources and availability
系统 SHALL 使用 Compustat 日频及历史标识、WRDS TAQ 汇总，不依赖 Bloomberg；MUST 检查权限、按纽约交易日历及各来源发布日期确定范围，并区分实际覆盖、缺口、待发布日期及共同覆盖。

#### Scenario: Source unavailable
- **WHEN** 当前账号没有 Compustat 或 TAQ 读取权限
- **THEN** 系统明确报告受影响来源并返回非成功状态，不切换到 Bloomberg 或其他未经选择的源

#### Scenario: Different publication dates
- **WHEN** daily 与 TAQ 最新可用交易日不同或请求日期尚未发布
- **THEN** 报告分别列出来源覆盖及共同覆盖，尚未发布日期为 pending，不声称请求全区间完成

#### Scenario: Holiday or early close
- **WHEN** 更新区间包含休市日或半日交易日
- **THEN** 系统依据交易日历判断预期会话与收盘时间，休市不计为漏下载，未结束会话不作为完整日发布

### Requirement: Validated incremental partitions
系统 SHALL 检查完整请求范围，下载缺失或损坏分区、刷新默认最近 5 个已发布交易日涉及分区，并支持配置刷新范围。只有来源、区间、版本、行数和指纹匹配的有效分区 SHALL 被跳过。

#### Scenario: Gap before latest date
- **WHEN** 最大本地日期之前存在缺失日或损坏文件
- **THEN** 系统补齐或重取该分区，而不只从最大日期之后开始

#### Scenario: Repeated unchanged update
- **WHEN** 已有完整有效分区且不处于刷新范围
- **THEN** 系统复用该分区，不重复下载或产生重复主键

#### Scenario: Monthly extension
- **WHEN** 月中已有部分 daily 数据且后续更新扩展本月日期
- **THEN** 系统保存已有历史与新日期的并集，并更新真实覆盖元数据

#### Scenario: Legacy or empty partition
- **WHEN** 发现无来源指纹的旧 TAQ 文件或预期交易日的空结果
- **THEN** 系统不将其仅凭文件存在视为可信完成，执行校验/重取或报告来源缺口

### Requirement: Recoverable publication
系统 MUST 原子发布分区并最后记录完成元数据，限制同目录并发写入；中断或部分失败 MUST 保留已验证分区、返回非成功状态，并允许重跑恢复。处理失败 MUST 保留上一套有效输出。

#### Scenario: Interrupted write
- **WHEN** 下载或处理在临时文件阶段中断
- **THEN** 临时数据不被视为完整，重跑恢复，之前有效产物仍可使用

#### Scenario: Concurrent update
- **WHEN** 两个更新进程尝试写同一数据目录
- **THEN** 系统串行保护或明确拒绝第二个写入，避免分区及元数据交叉覆盖

### Requirement: Reusable WRDS-only processed datasets
系统 SHALL 提供无需联网的处理入口，输出 `Data/processed/us_wrds_daily/` 日频表及 `Data/processed/us_wrds_taq/` 检查点、竞价标签和日度汇总，附带 schema、单位、来源及质量清单。系统 MUST 保留原生标识、价格状态和调整信息，不伪造缺失字段，不静默连接歧义证券，不将原生观察表标为已滞后的模型特征。

#### Scenario: Process without Bloomberg
- **WHEN** 仅有已验证 WRDS 原始分区，且 Bloomberg 目录不存在
- **THEN** 系统可离线生成所有新模块约定的处理产物，主键分别为证券日及证券日检查点

#### Scenario: Missing or ambiguous observations
- **WHEN** 股数、日内收益或跨源永久身份不可得
- **THEN** 系统保留缺失或歧义状态，不填零、伪造收益或强行连接，并在质量报告记录

#### Scenario: Changed inputs
- **WHEN** 原始分区指纹或处理版本变化
- **THEN** 系统重建受影响来源的产物，读盘验证主键、日期、行数及数值后才发布完成清单

### Requirement: Volume validity and expected security sessions
系统 MUST 在独立质量层将日频总成交量、上市场所收盘竞价标签（EXCHG=11 取 TAQ `xvol_n`，EXCHG=14 取 `xvol_q`，不回退其他场所或最大场所）任一 ≤0、NA、无穷的股票日标记为异常；两者均有限且 >0 才标记有效。系统 SHALL 按请求范围、有效上市/市场区间和共同已发布覆盖的交集生成应有交易日网格，并从中扣除重大指数再平衡执行收盘日与 XNYS 半日市（见 `auction-research-dataset`）；区间内缺记录计异常，上市前/退市后、休市、屏蔽日及待发布日不计；上市边界无法验证或下载未完成时 MUST 报告未决，不能误作合格或批量剔除。

#### Scenario: Either volume invalid
- **WHEN** 日频总成交量或上市场所竞价标签中任意一个为 0、负数、NA 或无穷
- **THEN** 该股票日无效并记录具体字段及异常原因，即使另一成交量为正常正数

#### Scenario: Missing expected record
- **WHEN** 已发布且下载完整的有效上市区间内某非屏蔽应有交易日缺证券记录
- **THEN** 该日计入分母及异常日，上市前/退市后、休市日及屏蔽日不计入

#### Scenario: Source incomplete
- **WHEN** 分区下载失败或身份/上市边界无法确认
- **THEN** 系统报告质量计算未完成或未决，不将下载故障解释为股票自身异常

### Requirement: Prior ten session cross-sectional eligibility
系统 SHALL 使用不含当日的过去 10 个 XNYS 交易日中的有效日标记，至少 6 天有效才允许进入当日横截面；MUST 保留日历中的缺失会话；屏蔽日占用窗口位置且不计为有效，MUST NOT 向前补足；最终可用记录还必须当天有效。初期不足 10 天时 SHALL 按实际可见历史计数，绝对门槛仍为 6 天。

#### Scenario: Six valid days
- **WHEN** t 日之前的 10 个交易日恰好有 6 个有效日，股票通过整股筛选且 t 日有效
- **THEN** t 日记录被保留

#### Scenario: Five valid days or invalid current day
- **WHEN** 过去窗口只有 5 个有效日，或窗口达标但 t 日异常
- **THEN** t 日记录不进入最终可用样本，分别记录历史不足或当日异常原因

#### Scenario: Masked session in window
- **WHEN** t 日之前 10 个会话中有 1 个屏蔽日，其余 9 个中 5 个有效
- **THEN** t 日不合格，系统不向更早会话补足

#### Scenario: No current day leakage
- **WHEN** t 日由有效改为异常
- **THEN** t 日的历史有效天数不变，t 日变化只影响当日有效性及以后窗口

#### Scenario: Short history
- **WHEN** 可见历史不足 10 个交易日且只有 5 个有效日
- **THEN** 系统不降低为比例门槛，t 日无横截面资格

### Requirement: Full history security exclusion
系统 SHALL 在行级过滤前计算全历史异常日数 / 应有交易日数（分母已扣除屏蔽日），严格 >5% 时剔除整只证券，恰好 5% 保留；零分母或身份未决 MUST 标为 unresolved，并在本次研究中暂缓使用（见 `auction-research-dataset`）。系统 MUST 保留原生数据和质量明细，并在清单标注全历史事后筛选及严格历史回测限制；严格 PIT 资格由独立字段提供。新增或修订数据 SHALL 触发受影响证券全区间质量重算。

#### Scenario: Threshold boundary
- **WHEN** 某证券 100 个应有交易日有 5 个异常，另一证券有 6 个异常
- **THEN** 前者通过整股筛选但仍需逐日判断，后者全部日期从事后样本剔除

#### Scenario: Filtering order
- **WHEN** 部分异常日已不满足滚动资格
- **THEN** 这些日期仍参与全历史异常率计算，不先删除再计算分母

#### Scenario: Revised eligibility and audit trail
- **WHEN** 更新数据导致全历史异常率跨越 5% 门槛
- **THEN** 系统更新该证券全部日期的最终资格，保存分子分母、输入指纹和规则版本，并标注该结果使用全历史信息

### Requirement: Local data and credential privacy
仓库 MUST 通过根目录 `/Data/` 忽略规则覆盖全部原始和处理数据；模块 MUST 不生成受 Git 跟踪的个人认证文件，示例及源码不得包含个人账号或真实凭证。

#### Scenario: Git staging
- **WHEN** 用户按常规方式执行 Git 添加操作
- **THEN** `Data/raw/` 和 `Data/processed/` 文件被忽略，且交付检查确认无已跟踪 Data 文件

#### Scenario: Repository examples
- **WHEN** 检查下载入口、认证代码和使用文档
- **THEN** 账号以运行时输入或占位符表示，不包含开发者个人账号

### Requirement: Retire legacy datasets and research notebooks
迁移完成后项目 MUST 不再保留 `Data/processed/us/`、`Data/processed/lse/`、`Data/raw/bloomberg/uk/`、研究 notebook 及对应 checkpoint；MUST 清理已废弃入口和专属文档及其他文档的失效引用。删除 SHALL 按显式路径清单执行，不扩大到其他美股 raw、独立 WRDS 产物或实验结果目录；共享逻辑如仍必需 MUST 先迁移。

#### Scenario: Migration cleanup
- **WHEN** 新 WRDS-only 模块验证完成并执行迁移清单
- **THEN** 指定旧数据、研究 notebook 及废弃入口被移除，其他美股 raw/独立 WRDS 数据保留，README 指向可用的新模块

#### Scenario: No legacy fallback
- **WHEN** 新模块运行失败
- **THEN** 系统报告失败或使用自身上一套有效 WRDS 产物，不回退至已移除的 LSE 或混合数据流程

