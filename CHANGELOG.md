# Changelog

## 0.3.0

### Added

- 三层评测：检索（61 题 recall@k/MRR，按 single_doc/table/scan 分类）、生成（12 正 + 8 负，正确率/引用率/拒答率）、Agent 行为（工具路由/完成率/步数/token）。
- 独立评测 API：`/api/v1/eval/{retrieval,generation,agent,summary}`。
- 生成评测文档与数据集：`docs/evaluation.md`、`data/eval/golden_generation.json`。
- 工程化骨架：pytest 测试、GitHub Actions CI、Makefile、pyproject、ADR。
- 逐阶段 trace span（parse / retrieval / generation / tool / agent.run）与 Prometheus `/metrics`。
- Docker 非 root 运行。

### Changed

- RAG 检索评测从 17 题扩到 61 题，报告增加 `by_category`。
- Agent 评测也整体 bypass 检索缓存。
- 旧 `/rag/*-eval` 保留为隐藏兼容别名，Swagger 只展示 `/eval/*`。
