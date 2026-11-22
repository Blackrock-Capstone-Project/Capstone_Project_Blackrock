# Equity Closing Auction — WRDS 数据模块

从 WRDS 获取自 **2022-01-03** 起的 NYSE/Nasdaq 历史日频及 TAQ 日内汇总，校验后保存到本地 `Data/processed/`。后续调用同一方法即可补洞、追加并刷新近期数据。所有命令在仓库根目录运行。

```bash
python3 -m venv .venv-forecast
.venv-forecast/bin/python -m pip install -r scripts/download/requirements-wrds.txt
.venv-forecast/bin/python -m scripts.data_pipeline update
```

也可以打开 [下载与更新 notebook](scripts/download/download_wrds.ipynb)，按 cell 执行首次下载、离线处理和后续更新；它使用适配 Jupyter 的运行时认证回调。

运行时输入自己的 WRDS 账号和隐藏密码，并在需要时完成 Duo 登录批准。模块不保存凭证，也没有个人账号默认值。账号必须有 Compustat 与 TAQ 权限。

首次下载可先选一个小区间验证：

```bash
.venv-forecast/bin/python -m scripts.data_pipeline update --start 2022-01-03 --end 2022-01-14 --markets NYSE
```

持续更新、离线重处理、审计与快照比较：

```bash
.venv-forecast/bin/python -m scripts.data_pipeline update
.venv-forecast/bin/python -m scripts.data_pipeline process
.venv-forecast/bin/python -m scripts.data_pipeline audit
.venv-forecast/bin/python -m scripts.data_pipeline compare --old <旧快照或版本目录> --new <新快照或版本目录> --out <输出目录>
```

Python 调用：

```python
from scripts.data_pipeline import update_data, process_data, audit_data
report = update_data(start="2022-01-03", markets=("NYSE", "NASDAQ"))
print(report["complete"], report["sources"])
```

`Data/` 全部被 Git 忽略。原始分区位于 `Data/raw/wrds/daily/` 和 `Data/raw/taq/days/`；处理结果分别在 `us_wrds_daily`、`us_wrds_taq`、`us_wrds_quality`。通过 `Data/processed/wrds_current/manifest.json` 核验当前版本。日内数据为服务器端聚合的检查点和竞价数据，不是完整逐笔存档。

研究样本只含 Compustat `tpci='0'` 普通股；标签为当日上市交易所的收盘竞价量（NYSE 取 `xvol_n`，Nasdaq 取 `xvol_q`）。重大指数再平衡执行日（清单见 `scripts/data_pipeline/reference/index_events.csv`）和半日市退出样本、滚动窗口与 5% 分母。质量筛选要求日频总成交量和竞价标签均有限且 >0；过去 10 个交易日（不含当日，屏蔽日不补足）至少 6 天有效；全历史异常率 >5% 整只剔除（事后口径）。另提供只用 t−1 以前数据的 PIT 资格计数，以及用 `ajexdi_s / ajexdi_t` 把历史股数换算到预测日股本口径所需的字段。质量报告在 `Data/processed/report/`。

从干净 checkout 复现、验证一次运行、以及重新下载后识别和解释差异，见 [复现与差异核查](docs/REPRODUCIBILITY.md)；待核实事项见 [OPEN_ITEMS](docs/OPEN_ITEMS.md)。

完整输出契约、更新/失败恢复、认证和限制见 [数据流程说明](docs/WRDS_DATA_PIPELINE.md)。旧混合处理产物、LSE 流程及研究 notebook 已退出项目；`results/` 仅保留历史实验产物。
