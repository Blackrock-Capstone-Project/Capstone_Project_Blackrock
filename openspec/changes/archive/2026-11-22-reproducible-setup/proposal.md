## Why

仓库尚无提交。需要让拥有相同 WRDS 权限的研究者从干净 checkout 完成下载、处理与验证；相同输入快照与配置应得到相同结果，而重新下载可能因 WRDS 修订产生差异，需要识别与解释差异的方法。

## What Changes

- `.gitignore`：排除凭据、原始与处理数据、本地环境、缓存、系统文件及含行级 WRDS 原值的历史诊断文件。
- 新增只读 `compare` 命令：比较两个数据快照的 raw 分区（指纹、行数、键与字段级差异）和两个处理版本（规则版本、事件清单、产物指纹、研究样本行级差异）。
- 新增复现文档：环境、首次下载、处理、验证、确定性范围、更新前快照与差异解释流程。
- 清除 notebook 已保存的输出。

## Capabilities

### New Capabilities

- `reproducible-runs`: 干净 checkout 可复现运行与快照差异比较。

### Modified Capabilities

无。

## Impact

新增 `scripts/data_pipeline/compare.py`、CLI 子命令、测试、`docs/REPRODUCIBILITY.md`；修改 `.gitignore`、README、notebook 输出。不改变任何处理口径或 `PROCESS_VERSION`。
