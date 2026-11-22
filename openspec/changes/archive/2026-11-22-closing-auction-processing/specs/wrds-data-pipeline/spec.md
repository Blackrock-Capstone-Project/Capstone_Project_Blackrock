## MODIFIED Requirements

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
