## Context

处理已有输入指纹复用和逐字节可复现的 gzip 输出；`.wrds_versions/` 保留历次处理版本；raw 分区旁 `.json` 记录 SHA256、行数、请求与下载时间。`update` 会就地重取近期分区，因此 raw 差异比较需要更新前的快照副本。

## Goals / Non-Goals

**Goals:** 首次提交即可复现；提供差异识别工具与流程。
**Non-Goals:** 不改处理口径；不做 raw 快照的自动归档（文档给出 APFS/rsync 方法）。

## Decisions

- **D1 只读比较模块**：`compare.py` 只读 `raw/**.json` 元数据和必要的分区内容、处理版本的 `manifest.json` 与 `research_sample`；输出 JSON 摘要与可选 CSV 明细到用户指定目录。备选是把比较塞进 `process`——会让发布路径变复杂。
- **D2 两种输入自动识别**：含 `raw/` 的目录按数据快照比较；含 `manifest.json` 的目录按处理版本比较。
- **D3 键**：daily `gvkey,iid,datadate`；TAQ `taq_symbol,date`；history `gvkey,iid,item,efffrom`；研究样本 `security_id,date`。
- **D4 行级原值文件不入库**：`results/` 中 `label_link_reviews.csv.gz`、`mck_gap_check.csv` 含逐行价格/竞价量，忽略；聚合指标保留。

## Risks / Trade-offs

- [全量 TAQ 分区逐行比较耗时] → 只对指纹不同的分区读内容。
- [PNG 跨机器不逐字节一致] → 文档声明，比较时跳过图表。
