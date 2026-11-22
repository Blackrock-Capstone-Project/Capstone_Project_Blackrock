# Equity Closing Auction — WRDS 数据模块

从 WRDS 获取自 **2022-01-03** 起的 NYSE/Nasdaq 日频（Compustat）与 TAQ 日内汇总，校验、版本化后生成收盘竞价成交量预测的研究样本。所有命令在仓库根目录运行；需要拥有 Compustat 与 TAQ 权限的 WRDS 账号，运行时输入账号与隐藏密码，需要时完成 Duo 批准，模块不保存凭证。

```bash
python3.13 -m venv .venv-forecast
.venv-forecast/bin/python -m pip install -r requirements.txt
.venv-forecast/bin/python -m unittest                      # 离线测试
.venv-forecast/bin/python -m auction_data check            # 只读：登录、权限与发布日期
.venv-forecast/bin/python -m auction_data update           # 下载/补洞/刷新 + 处理
.venv-forecast/bin/python -m auction_data process          # 离线处理（同一输入复用已有版本）
.venv-forecast/bin/python -m auction_data audit            # raw 完整性
.venv-forecast/bin/python -m auction_data compare --old <旧快照或版本目录> --new <新快照或版本目录> --out <输出目录>
```

也可以用 [notebook](notebooks/download_wrds.ipynb) 逐 cell 执行首次下载、离线处理、更新与预览。

## 文档

| 文档 | 内容 |
|---|---|
| [数据报告](docs/DATA_REPORT.md) | 数据源、范围、身份匹配、标签、筛选、事件日、PIT 与事后口径、暂缓证券、处理结果、质量检查、已知限制 |
| [数据流程说明](docs/WRDS_DATA_PIPELINE.md) | 输出契约、更新与失败恢复、认证与隐私、研究样本口径 |
| [复现与差异核查](docs/REPRODUCIBILITY.md) | 干净 checkout 复现、运行验证、确定性范围、重新下载后的差异识别与解释 |
| [待核实事项](docs/OPEN_ITEMS.md) | 未验证事件日期、待改进与后续项 |
| [规格](openspec/specs/) | `wrds-data-pipeline`、`auction-research-dataset`、`reproducible-runs` |

## 结构

```
auction_data/          python -m auction_data
  settings.py          版本、市场代码、检查点、场所常量
  trading_calendar.py  XNYS 会话、收盘时间、半日市
  storage.py           原子写入、raw 分区与元数据、指纹、写锁、表校验
  wrds_auth.py         运行时登录（不保存凭证）
  wrds_queries.py      Compustat 日频/历史标识与 TAQ 汇总 SQL、来源校验
  pipeline.py          update / process / audit 入口，分区计划与执行
  identity.py          Compustat↔TAQ 符号键、证券类型、逐证券上市网格与匹配
  labels.py            上市场所收盘竞价标签与场所对账
  events.py            指数再平衡执行日与半日市屏蔽
  eligibility.py       有效日、10 日 6 有效、事后 5%、严格 PIT、研究纳入
  splits.py            AJEXDI 拆股换算与未决标记
  build.py             日频清洗、逐证券流式构建、日内检查点
  publish.py           版本目录、读盘校验、wrds_current 原子切换
  report.py            质量报告；dictionary.py 字段字典；compare.py 快照比较
  reference/index_events.csv   已核验的指数事件清单（规则输入）
tests/                 离线测试（模拟 WRDS）
notebooks/             下载与更新 notebook
docs/                  文档；docs/reference/ 课题报告
openspec/              规格与已归档变更
```

`Data/`（raw 分区、处理版本、运行报告）全部被 Git 忽略。研究训练使用 `Data/processed/us_wrds_quality/research_sample.csv.gz` 中 `research_included_pit` 为真的行；每次实验记录所用筛选条件、处理版本目录与 Git 提交。
