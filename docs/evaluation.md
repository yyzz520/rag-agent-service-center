# 评测说明

评测分三层：**检索、生成、Agent 行为**。三层都固定题集、可重复运行，用来做版本对比和回归门禁，不用"感觉好像变好了"来判断。

## 1. 检索评测（61 题）

- 数据集：`data/eval/golden.json`，61 题，覆盖 `single_doc` / `table` / `scan` 三类。
- 成本：只重放检索链路，不调生成模型，零 LLM 花费。
- 指标：`recall@k`、`MRR`；按题型拆分 `by_category`。
- 实测（9 份语料、10 个 chunk）：

| 配置 | recall@1 | recall@5 | recall@16 | MRR |
|---|---|---|---|---|
| rerank 开 | 1.000 | 1.000 | 1.000 | 1.000 |
| rerank 关 | 0.590 | 1.000 | 1.000 | 0.775 |

按题型（rerank 关 → 开）：扫描件 0.333 → 1.0，表格 0.625 → 1.0，单文档 0.6 → 1.0。

## 2. 生成评测（12 正 + 8 负）

- 数据集：`data/eval/golden_generation.json`。
- 正样本：对照参考答案，用 LLM-as-judge 判断答案是否正确、是否带引用。
- 负样本：问文档里没有的内容，用拒答话术 + 是否带引用做确定性判定。
- 成本：真跑 RAG 生成 + 裁判模型，有 token 花费。

实测：

| 指标 | 结果 |
|---|---|
| 正样本正确率 | 11/12 = 0.917 |
| 引用率 | 12/12 = 1.0 |
| 负样本拒答 | 8/8 都拒答 |
| 干净拒答率 | 7/8 = 0.875（1 条拒答但仍带引用）|

已暴露的问题：常规用法用量漏了"可调整至 60mg / 整片吞服"；儿童剂量题拒答但带了引用。这两条是下一轮要修的，不是"指标好看就结束"。

## 3. Agent 行为评测（5 任务）

- 数据集：`data/eval/agent_tasks.json`。
- 真跑 LLM，评工具路由正确率、完成率、平均步数、平均 token。
- 实测：路由 1.0、完成率 1.0、平均 1 步、约 2708 token。

## 怎么跑

```bash
# 管理员登录后
POST /api/v1/rag/eval          # 检索评测，可传 candidate_pool/per_query_top/max_docs/use_rerank
POST /api/v1/rag/gen-eval      # 生成评测
POST /api/v1/rag/agent-eval    # Agent 行为评测

GET  /api/v1/rag/eval          # 历史
GET  /api/v1/rag/gen-eval
GET  /api/v1/rag/agent-eval
```

## 口径与边界

- **这是固定回归集，不是全量 benchmark**。61 题、12+8、5 任务都用于同参对比和回归门禁，不足以证明统计显著性。
- 真实评估应按文档类型/题型分层扩到几百题，并加 holdout、负样本和人工复核。
- RAG 检索评测和生成评测会整体绕过检索缓存（`cache.bypass()`）；Agent 行为评测目前**不 bypass**，缓存命中会让结果偏乐观，这是已知边界。
- 正样本用 LLM 裁判，必须配合人工抽检；不能把裁判分数当绝对真值。
