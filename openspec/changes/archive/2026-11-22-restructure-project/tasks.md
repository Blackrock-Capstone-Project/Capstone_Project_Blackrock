## 1. 包与模块

- [x] 1.1 建立 `auction_data/` 并按 D2 迁移函数（AST 原样提取），`check` 子命令
- [x] 1.2 删除旧脚本、旧测试、`results/`、重定向与迁移文档；移动 notebook、依赖文件、参考报告

## 2. 测试

- [x] 2.1 `tests/` 按模块拆分，helpers mixin；用例逐条保留并通过

## 3. 文档

- [x] 3.1 README、WRDS_DATA_PIPELINE、REPRODUCIBILITY、OPEN_ITEMS 与 notebook 更新到新入口
- [x] 3.2 详尽数据报告 `docs/DATA_REPORT.md`

## 4. 一致性验证

- [x] 4.1 新代码独立重建同一 raw 快照，与基线版本比较；真实 `Data/` 上 `process` 复用、`audit`、`check --help`、notebook 单元
