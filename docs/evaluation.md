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
| rerank 关 | 0.590 | 0.984 | 1.000 | 0.772 |

补充：rerank 关时 precision@16 = 0.062、NDCG@16 = 0.83。每题只有一个期望块，precision@16 的分母是 61×16，所以数值看起来小是正常的。

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
| 干净拒答率 | 8/8 = 1.000 |
| 引用正确率 | 12/12 = 1.000 |
| 引用错误 / 缺失 | 0 / 0 |

已暴露的问题：常规用法用量漏了“可调整至 60mg / 整片吞服”（最新一轮 11/12）；引用卫生在更早一轮抓到过（儿童剂量题拒答但带引用），最新一轮引用正确率 12/12。评测会随模型调用波动，以最新报告为准。

## 3. Agent 行为评测（8 任务）

- 数据集：`data/eval/agent_tasks.json`。
- 真跑 LLM，评工具路由正确率、完成率、平均步数、平均 token、负样本拒答率、禁工具调用率。
- 实测：路由 1.0、完成率 1.0、平均 0.88 步、约 3071 token；负样本 3/3 干净拒答，禁工具任务 0 调用。
- Agent 评测会随模型调用波动，以最新报告为准；上一轮曾抓到 1 条完成失败，说明指标有区分度，不是恒为 1.0。

## 4. Badcase 诊断

`GET /api/v1/eval/diagnosis` 读最近一次三层评测，按规则归因：

- 没召回 → 解析/切块/召回；
- 召回了但 rank > 1 → rerank/排序；
- 召回正确但答案错 → 生成/prompt；
- 负样本拒答但带引用 → 引用卫生；
- 裁判失败 → 评测自身问题；
- Agent 失败且调了知识库 → 优先怀疑检索召回/切块；
- Agent 失败且没调知识库 → 怀疑路由/生成/工具返回。

诊断是“下一步改哪层”的线索，不是因果断言；不调模型、不重跑检索。

## 怎么跑

```bash
# 管理员登录后
POST /api/v1/eval/retrieval     # 检索评测，可传 candidate_pool/per_query_top/max_docs/use_rerank
POST /api/v1/eval/generation    # 生成评测
POST /api/v1/eval/agent         # Agent 行为评测

GET  /api/v1/eval/retrieval     # 历史
GET  /api/v1/eval/generation
GET  /api/v1/eval/agent
GET  /api/v1/eval/summary       # 三层最新指标汇总
GET  /api/v1/eval/diagnosis     # 最近一次评测的规则化 badcase 归因
```

## 评测门禁

`make eval-gate` 读最近一次检索/生成评测报告，和 `data/eval/baseline.json` 的阈值比较；低于阈值非 0 退出：

| 层 | 指标 | 阈值 |
|---|---|---|
| 检索 | recall@1 | 0.55 |
| 检索 | MRR | 0.70 |
| 生成 | correct_rate | 0.85 |
| 生成 | citation_rate | 0.90 |
| 生成 | refusal_rate | 0.75 |

门禁只读报告、不重跑评测；报告缺失或指标缺失都算失败。

## 回流微调

`make export-finetune` 读最近一次生成评测，只导出：

- 正样本：`correct=True` 且 `citation_correct=True`；
- 负样本：干净拒答（`reason=refused`）。

输出：
- `data/eval/finetune.jsonl`：RAG 答案样本，格式 `{"instruction","input","output"}`，去掉答案里的 `[n]` 引用标记；
- `data/eval/agent_finetune.jsonl`：Agent 轨迹，格式 `{"messages": [...]}`，包含 user / assistant（含 `tool_calls`）/ tool 消息；只导成功且路由正确的任务。

边界：这是当前实现的数据回流；生产上要人工复核、去重、隐私脱敏、版本化。不要把模型自己的回答无条件回灌，否则会放大错误。Agent 轨迹里的工具结果已截断到 2000 字。

## 口径与边界

- **这是固定回归集，不是全量 benchmark**。61 题、12+8、8 任务都用于同参对比和回归门禁，不足以证明统计显著性。
- 真实评估应按文档类型/题型分层扩到几百题，并加 holdout、负样本和人工复核。
- 三层评测都会整体绕过检索缓存（`cache.bypass()`）：命中缓存会让分数虚高且不可复现，评测必须打真实链路。
- 正样本用 LLM 裁判，必须配合人工抽检；不能把裁判分数当绝对真值。
