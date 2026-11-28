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

**小样本口径**：报告里同时给 `recall@k` / `MRR` 的 95% bootstrap 置信区间（`metrics.ci95`，固定种子可复现）。61 题的区间通常比点估计宽，汇报时要一起给，不能只挑好看的均值。

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

**统计口径**：报告带正确率/引用率/拒答率的 95% bootstrap 区间（`metrics.ci95`）。`POST /rag/gen-eval` 传 `repeat>1` 可同题多跑 N 轮（成本 ×N），报告额外给 `stability`（min/max/std）；默认 `repeat=1`，稳定性按需开。

## 3. 意图路由评测（14 题）

- 数据集：`data/eval/router_tasks.json`，覆盖 `chat` / `rag` / `tool`，含"没有外部工具时 tool 退回 rag"的用例。
- 成本：只跑规则分类，**零 LLM 花费**。
- 指标：准确率、各路由 precision/recall/F1、混淆矩阵；报告带 95% bootstrap 区间与题集指纹。
- 实测：14/14 = 1.000（固定小集，用于回归关键词与优先级，不代表开放域准确率）。
- 改关键词/优先级后 `make eval-router` 重跑即可回归；接模型分类器时保持同一题集对比。

## 4. Agent 行为评测（8 任务）

- 数据集：`data/eval/agent_tasks.json`。
- 真跑 LLM，评工具路由正确率、完成率、平均步数、平均 token、负样本拒答率、禁工具调用率。
- 实测：路由 1.0、完成率 1.0、平均 0.88 步、约 3071 token；负样本 3/3 干净拒答，禁工具任务 0 调用。
- Agent 评测会随模型调用波动，以最新报告为准；上一轮曾抓到 1 条完成失败，说明指标有区分度，不是恒为 1.0。

## 5. Plan-and-Execute 评测（5 任务）

- 数据集：`data/eval/plan_tasks.json`，多步任务（查批号 + 查温控规范 + 判断合规；对比两个批号；近效期管理等），全部使用**虚构医药+仓储数据**。
- 成本：真跑计划生成 + 工具执行 + 合成答案，**有 token 花费**。
- 指标：任务成功率（期望工具都执行 + 答案包含关键事实 + 步数不超限）、平均步数、平均 token、平均耗时；报告带 bootstrap 区间、题集指纹、`plan/plan_synth` 版本。
- 实测：5/5 成功，平均 1.8 步、2877 token、16.1 秒（固定小集，用于回归与选型，不代表开放域成功率）。
- 评测用 MemorySaver + 独立 thread，自动 approve，不碰线上会话；HITL 审批链路由单元测试覆盖。

## 5. 单变量 A/B 实验

`make eval-experiment NAME=rerank` 对同一题集跑 baseline / variant，**只改一个变量**，输出指标差值与配对 bootstrap 区间。报告存 `data/eval/experiments.jsonl`，工作台「评测数据」直接展示对比表。

可用实验：

| name | 变量 | 任务 | 成本 |
|---|---|---|---|
| `rerank` | rerank 关 vs 开 | 检索重放 | 低 |
| `refusal_finalize` | 拒答收口 关 vs 开 | 真跑生成 | 有 token |
| `plan_schema` | 计划提示词是否注入工具 schema | 真跑规划+执行 | 有 token |

本地当前索引上 rerank 实测：MRR 0.366→0.869（Δ+0.503，95% CI [0.445, 0.553]），recall@1 0→0.869，recall@16 0.869→0.869。

口径：

- 报告带 `comparable`（题集 sha256 一致才算可比）和 `prompt_version`
- 配对区间按题目对齐，不是两组独立样本的区间
- **当前本地索引是部分重建状态**，绝对值和历史报告不可直接比；A/B 只在同一索引快照内可比。全量 `make reindex` 后再跑一轮才是完整基线

API：`POST /api/v1/eval/experiments`（body: `{"name":"rerank"}`）、`GET /api/v1/eval/experiments`。

## 6. Badcase 诊断

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
POST /api/v1/eval/router        # 意图路由评测（规则分类，零花费）
POST /api/v1/eval/plan          # Plan-and-Execute 窄场景评测（真调 LLM，有花费）

GET  /api/v1/eval/retrieval     # 历史
GET  /api/v1/eval/generation
GET  /api/v1/eval/agent
GET  /api/v1/eval/router
GET  /api/v1/eval/plan
GET  /api/v1/eval/summary       # 三层最新指标汇总
GET  /api/v1/eval/diagnosis     # 最近一次评测的规则化 badcase 归因
POST /api/v1/eval/experiments   # 单变量 A/B（body: name=rerank/refusal_finalize/plan_schema）
GET  /api/v1/eval/experiments   # A/B 实验历史
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

导出前做最小清洗：去重、8–4000 字长度过滤、手机号/身份证/邮箱/密钥脱敏、引用编号清理；同一题多轮评测一正一误会额外导出 `data/eval/preferences.jsonl` 的 DPO 偏好对（chosen/rejected）。

边界：生产上还要人工复核；不要把模型自己的回答无条件回灌，否则会放大错误。Agent 轨迹正文同样做 PII 脱敏，工具结果在评测序列化里截断到 2000 字。

## 口径与边界

- **这是固定回归集，不是全量 benchmark**。61 题、12+8、8 任务都用于同参对比和回归门禁，不足以证明统计显著性；报告给点估计 + 95% 区间，同题多跑看波动，但不下"显著提升"的结论。
- 真实评估应按文档类型/题型分层扩到几百题，并加 holdout、负样本和人工复核。
- 三层评测都会整体绕过检索缓存（`cache.bypass()`）：命中缓存会让分数虚高且不可复现，评测必须打真实链路。
- 正样本用 LLM 裁判，必须配合人工抽检；不能把裁判分数当绝对真值。
