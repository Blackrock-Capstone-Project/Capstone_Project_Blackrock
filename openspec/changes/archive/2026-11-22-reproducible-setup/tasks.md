## 1. 仓库卫生

- [x] 1.1 更新 `.gitignore` 并确认 `git add -A` 的暂存内容
- [x] 1.2 清除 notebook 已保存输出

## 2. 差异比较

- [x] 2.1 实现 `compare.py` 与 `python -m scripts.data_pipeline compare OLD NEW [--out DIR]`
- [x] 2.2 测试：raw 分区修订、仅旧/仅新分区、处理版本研究样本差异、输入不被修改

## 3. 复现文档与验证

- [x] 3.1 `docs/REPRODUCIBILITY.md` 与 README 链接
- [x] 3.2 首次提交前以待提交文件的干净副本运行测试，并用同一 raw 快照独立重建，与当前版本比较
