# WRDS-only 迁移记录

本次只清理指定旧流程；没有执行真实 WRDS 登录、全量下载或模型训练。

## 删除清单

- `Data/processed/us/`：删除前 11 个文件。
- `Data/processed/lse/`：删除前 11 个文件。
- `Data/raw/bloomberg/uk/`：删除前 3161 个文件。
- `scripts/processing/process_lse_auction_data.py`：已移除。
- `scripts/processing/process_nyse_auction_data.py`：已移除。
- `scripts/processing/process_us_taq_data.py`：已移除。
- `scripts/download/bbg_auction_pull.py`：已移除。
- `docs/LSE_AUCTION_DATA_PIPELINE.md`：已移除。
- `docs/NYSE_AUCTION_DATA_PIPELINE.md`：已移除。
- `docs/UK_LSE_下载指南.md`：已移除。
- `docs/US_NYSE_NASDAQ_下载指南.md`：已移除。
- `docs/US_AUCTION_DATA_PIPELINE.md`：已移除。
- `docs/AGENT_WALKTHROUGH_BRIEF.md`：已移除。
- 研究 notebook 与对应 checkpoint：当前工作区未发现 `.ipynb`，无需删除。

## 保留检查

- `Data/raw/taq`：保留。
- `Data/raw/wrds`：保留。
- `Data/raw/bloomberg/us`：保留。
- `Data/processed/us_wrds_daily`：保留。
- `results`：保留。

其他美股 Bloomberg 原始数据按明确的删除范围之外保留，正式模块不读取它。实验结果目录仅作为历史快照。新模块仅使用 WRDS；旧混合流程不再作为回退路径。

## 验证边界

合成数据与模拟连接覆盖离线流程；真实 WRDS schema、权限及 Duo 需要用户在自己的交互终端执行小区间命令核验。既有本地多年数据未被自动升级或重处理，新模块首次运行会重取无新版元数据的旧分区。

本地验证：21 项新模块测试 + 8 项既有日频回归测试全部通过；Python 编译、CLI 帮助、文档链接、删除范围及 Git 忽略检查通过。OpenSpec 严格校验通过。
