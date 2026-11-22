# 复现与差异核查

面向拥有 WRDS 权限的研究者：从干净 checkout 完成下载、处理与验证，并在重新下载后识别、解释与上一快照的差异。口径与输出契约见 [数据流程说明](WRDS_DATA_PIPELINE.md)，数据内容与结果见 [数据报告](DATA_REPORT.md)，待核实事项见 [OPEN_ITEMS](OPEN_ITEMS.md)。

## 1. 前提

| 项 | 要求 |
|---|---|
| WRDS 权限 | Compustat North America Daily：`comp_na_daily_all.secd`、`comp_na_daily_all.sec_idhist`；TAQ Millisecond：`taqm_YYYY.ctm_YYYYMMDD`。使用自己的账号，需要时完成 Duo 批准 |
| 网络 | `wrds-pgdata.wharton.upenn.edu:9737`（PostgreSQL 协议） |
| Python | 3.13；依赖固定在根目录 `requirements.txt` |
| 资源（2022-01-03 至 2026-11-19 实测） | raw 约 730 MB；每个处理版本约 1.6 GB（历史版本不自动清理）；全量处理约 16 分钟、峰值内存约 18–22 GB |

仓库不含任何数据、凭证或本地环境（见 `.gitignore`）。

## 2. 从干净 checkout 运行

```bash
git clone <repo> && cd <repo>
python3.13 -m venv .venv-forecast
.venv-forecast/bin/python -m pip install -r requirements.txt

# 离线测试（无需 WRDS；真实数据样例测试在无 Data/ 时自动跳过）
.venv-forecast/bin/python -m unittest

# 只读检查：登录、来源权限与各来源最新发布日期（不下载）
.venv-forecast/bin/python -m auction_data check

# 小区间冒烟：确认账号权限、Duo 与 schema
.venv-forecast/bin/python -m auction_data update --start 2022-01-03 --end 2022-01-14 --markets NYSE

# 全量下载 + 处理（交互输入账号与隐藏密码）
.venv-forecast/bin/python -m auction_data update
# 或分开：先只下载，再离线处理与审计
.venv-forecast/bin/python -m auction_data update --download-only
.venv-forecast/bin/python -m auction_data process
.venv-forecast/bin/python -m auction_data audit
```

也可按 [下载与更新 notebook](../notebooks/download_wrds.ipynb) 逐 cell 执行。

## 3. 验证一次运行

1. `audit` 返回 `complete: true`（全部预期分区存在且指纹、行数、交易日覆盖通过）。
2. `Data/processed/wrds_current/manifest.json`：`version`（处理规则版本）、`quality_status: complete`、`deferred_securities` 与 `deferred_reasons`、`unverified_events`、`input_partitions`（每个 raw 分区路径与 SHA256）、`files`（每个产物 SHA256 与行数）。
3. `Data/processed/report/`：`security_status_summary.csv` 各层合计对齐；`funnel.csv`；`label_reconciliation.csv`；`summary.json`（PIT 与 ex-post 差异、与上一版本差异）；`daily_counts.png`。
4. 训练样本为 `us_wrds_quality/research_sample.csv.gz`；每个实验记录所用纳入标志（当前研究使用 `research_included_pit`）、处理版本目录名（`wrds_current` 指向的 `.wrds_versions/<id>`）、manifest `fingerprint` 与 Git 提交号。

## 4. 确定性范围

处理指纹 = raw 分区指纹 + 配置 + `PROCESS_VERSION` + 事件清单 SHA256。指纹相同则直接复用已有版本（`reused: true`）。

- **相同 raw 快照 + 相同配置 + 相同代码版本 → 逐字节相同的数据产物**（gzip 头 mtime 固定为 0，排序与格式确定）。首次提交前已用待提交文件的干净副本、对同一 raw 快照（2022-01-03 至 2026-11-19，1,286 个分区）独立重建：处理指纹相同，21 个数据产物 SHA256 与基线版本 `wrds-process-v3` 逐字节一致。
- `report/daily_counts.png` 依赖本机字体渲染，不作跨机器逐字节保证；其底表 `daily_counts.csv` 是确定的。
- `report/summary.json` 的 `diff_vs_previous` 取决于数据目录中是否已有上一处理版本（全新目录为 `previous: none`），其余字段确定。
- 修改处理规则代码必须递增 `auction_data/settings.py` 的 `PROCESS_VERSION`，否则会复用旧产物。`RAW_VERSION` 只标记 raw 分区格式，递增会触发全部重新下载。
- 事件清单 `auction_data/reference/index_events.csv` 的任何修改都会改变指纹并触发重建。

## 5. 重新下载为什么会不同

| 来源 | 机制 |
|---|---|
| WRDS 修订 | 已发布的 Compustat/TAQ 数据可能被供应商更正 |
| 近期刷新 | 每次 `update` 默认重取最近 5 个已发布交易日涉及的分区（日频为整月分区）；`--refresh-start` 可从指定日期重取 |
| 历史标识 | `sec_idhist`（EXCHG/TIC 生效区间）每次 `update` 都整表重取 |
| 新发布日期 | 截止日后移会新增分区，并改变全历史 5% 判定与 PIT 计数（ex-post 规则本身依赖全历史） |
| 事件清单 / 规则版本 | 清单修订或 `PROCESS_VERSION` 变化导致全量重建 |

## 6. 识别与解释差异

`compare` 命令只读，不修改输入：

```bash
# 1) 更新前保留 raw 快照（APFS 上 cp -c 为写时复制，几乎不占空间；其他系统用 rsync）
cp -cR Data/raw Data_snapshot_YYYYMMDD/raw    # 或: rsync -a Data/raw/ Data_snapshot_YYYYMMDD/raw/

# 2) 更新并处理
.venv-forecast/bin/python -m auction_data update

# 3) raw 层：哪些分区变了、哪些键与字段变了
.venv-forecast/bin/python -m auction_data compare \
    --old Data_snapshot_YYYYMMDD --new Data --out compare_raw_YYYYMMDD

# 4) 处理层：两个版本目录（历次版本都保留在 .wrds_versions/）
.venv-forecast/bin/python -m auction_data compare \
    --old Data/processed/.wrds_versions/<旧id> --new Data/processed/.wrds_versions/<新id> --out compare_processed_YYYYMMDD
```

输出：`summary.json` 与每个改变表的 `*.diff.csv`（新增、删除及改变行的新旧值，每表最多 1,000 行）。

- **raw 比较**：每个分区标 `identical` / `changed` / `only_old` / `only_new`；`changed` 给出新增/删除键数与逐字段改变行数（键：日频 `gvkey,iid,datadate`；TAQ `taq_symbol,date`；历史 `gvkey,iid,item,efffrom`）。
- **处理比较**：规则版本、指纹、配置、事件清单 SHA256、改变的输入分区、各产物是否一致，研究样本的新增/删除行与纳入标志、标签、成交量、证券状态的改变，以及证券级状态改变。

解释对照：

| 改变的字段 | 影响 |
|---|---|
| TAQ `xvol_n` / `xvol_q` | 研究标签与当日有效性 → 10 日窗口、5% 判定、PIT 计数 |
| TAQ `cumvol_*` | 日内检查点 |
| Compustat `cshtrd` | 日频有效性 |
| Compustat `ajexdi` / `prccd` | `split_status`、`split_segment_id`、跨日换算 |
| Compustat `tpci` | 普通股范围 |
| `sec_idhist` EXCHG / TIC | 上市区间、身份匹配、暂缓状态（`ticker_history_gap` 等） |
| 仅新增分区 | 截止日后移；全历史 5% 判定可能跨越阈值 |

处理层差异若无法由 raw 差异、事件清单或规则版本解释，应视为缺陷并报告。
