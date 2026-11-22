## ADDED Requirements

### Requirement: Single package entry point
项目 SHALL 以仓库根目录包 `auction_data` 提供唯一入口 `python -m auction_data`，子命令 `update`、`process`、`audit`、`compare`、`check`；仓库 MUST NOT 保留旧 `scripts/` 入口或通过 `sys.path` 拼接导入的脚本。

#### Scenario: Clean checkout commands
- **WHEN** 在干净 checkout 中按复现文档安装依赖并运行 `python -m auction_data --help` 与离线测试
- **THEN** 命令可用、测试通过，仓库中不存在 `scripts/` 目录

### Requirement: Restructure preserves outputs
结构调整 MUST NOT 改变处理口径：同一 raw 快照、配置、`PROCESS_VERSION` 与事件清单在新结构下 SHALL 产生与基线版本相同的处理指纹和逐字节相同的数据产物。

#### Scenario: Before and after comparison
- **WHEN** 用新结构对基线 raw 快照在独立目录重建并与基线处理版本比较
- **THEN** 指纹相同，全部数据产物 SHA256 一致
