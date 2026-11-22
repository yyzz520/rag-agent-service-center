# Changelog

## 0.3.0

### Added

- 三层评测：检索（61 题 recall@k/MRR，按 single_doc/table/scan 分类）、生成（12 正 + 8 负，正确率/引用率/拒答率）、Agent 行为（工具路由/完成率/步数/token）。
- 独立评测 API：`/api/v1/eval/{retrieval,generation,agent,summary}`。
- 生成评测文档与数据集：`docs/evaluation.md`、`data/eval/golden_generation.json`。
- 工程化骨架：pytest 测试、GitHub Actions CI、Makefile、pyproject、ADR。
- 逐阶段 trace span（parse / retrieval / generation / tool / agent.run）与 Prometheus `/metrics`。
- 规则化 badcase 诊断 `/api/v1/eval/diagnosis` 与 `make eval-gate` 评测门禁。
- Agent 评测扩到 8 条（含负样本/禁工具），增加拒答率与禁工具调用率。
- 检索评测增加 precision@k / NDCG@k；生成评测增加引用正确率（`citation_correct_rate`）。
- `make export-finetune` 导出 RAG 答案样本（instruction/input/output）和 Agent 轨迹（messages + tool_calls，含工具参数与截断后的工具结果）。
- Agent 评测报告新增 `trajectory` 字段，支持轨迹级数据回流。
- Docker 非 root 运行。

### Changed

- RAG 检索评测从 17 题扩到 61 题，报告增加 `by_category`。
- Agent 评测也整体 bypass 检索缓存。
- 旧 `/rag/*-eval` 保留为隐藏兼容别名，Swagger 只展示 `/eval/*`。
