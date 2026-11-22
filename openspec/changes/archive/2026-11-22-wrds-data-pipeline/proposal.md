## Why

现有下载流程包含 Bloomberg 依赖、个人 WRDS 账号、固定截止日期及不一致的续传规则，难以由其他使用者安全复用。需要建立从 2022 年开始、仅依赖 WRDS、可重复调用并持续更新的数据下载及处理模块，为收盘竞价研究提供可追溯的本地数据。

## What Changes

- 提供统一 Python 更新方法和命令行入口，协调认证、来源权限检查、下载、校验、处理与更新报告。
- 日频使用 WRDS Compustat，日内检查点与竞价标签使用 WRDS TAQ；正式流程无 Bloomberg 输入。
- 默认起点为 2022-01-03，支持指定截止日或探测各来源最新可用完整交易日。
- 支持首次回填、补洞、损坏分区重取、近期窗口刷新及失败续传。
- 运行时收集用户自己的账号并配合登录，不在源码、日志或数据清单中保存个人认证信息。
- 生成可复用的标准化日频、日内检查点、竞价标签及质量报告，保存至 `Data/processed/`。
- 增加成交量质量筛选：任一日频总量或竞价量 ≤0、NA、无穷即为异常日；过去 10 个交易日（不含当日）至少 6 天有效才具备当日横截面资格；有效上市区间全历史异常率 >5% 时整只剔除，标注事后筛选限制。
- 保留并验证已加入的 `/Data/` Git 忽略规则，下载数据不进入常规 Git 提交。
- **BREAKING**：新统一流程不再将旧 Bloomberg 混合产物作为权威输入；删除旧 `Data/processed/us/`、LSE 原始及处理数据、研究 notebook，并清理对应入口与文档引用。

## Capabilities

### New Capabilities

- `wrds-data-pipeline`: WRDS 交互认证、历史下载、增量维护、独立处理、质量验证及本地数据隐私保护。

### Modified Capabilities

无，当前没有已存在的主规格。

## Impact

涉及 `scripts/download/wrds_daily_pull.py`、`taq_auction_pull.py`、`wrds_taq_check.py`、`scripts/processing/process_wrds_daily_data.py`，以及新统一模块、测试和数据流程文档。复用现有 WRDS/PostgreSQL 客户端、Pandas 和交易日历依赖；具体版本在实施前核对。本次不开发或重跑预测模型；移除既有研究 notebook 和指定旧数据，保留其他美股原始数据及独立 WRDS 数据。不自动执行 Git 提交或全量下载。
