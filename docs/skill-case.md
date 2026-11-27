# Skill 案例说明（给 HR / 技术面试官）

## 一句话

项目里封装了两个可复用的 Agent Skill：**企业知识库问答** 和 **仓储批号状态查询**。Skill 不是再写一个函数，而是把“什么时候用、按什么步骤、调哪些工具、输出什么、失败怎么办”固化成可复用能力。

## Skill ≠ Prompt ≠ Tool

| 概念 | 解决什么 | 例子 |
|---|---|---|
| Prompt | 告诉模型“怎么答” | 问答/裁判/计划提示词 |
| Tool | 一个可调用的函数或接口 | `search_knowledge_base`、`get_batch_storage`、`release_batch` |
| **Skill** | **封装完整工作流：触发条件 + 步骤 + 工具 + 输出契约 + 失败处理** | 本目录两个 Skill |

## 两个 Skill

| Skill | 触发 | 依赖 | 输出 | 失败处理 | 证据 |
|---|---|---|---|---|---|
| `enterprise-kb-qa` | 制度/SOP/条款等文档问题 | RAG 检索 + 生成 | 带 `[n]` 引用的答案 + sources；拒答不带引用 | 无命中如实拒答；服务失败不伪造 | 61 题检索、12+8 生成评测 |
| `warehouse-batch-status` | 批号/库区/批记录/偏差/放行 | MCP 外部工具（查询 + 放行）+ 人工审批 | 库区/储存条件/QA 状态/批记录/偏差/结存 | 审批拒绝/超时不执行；放行条件不满足逐条说明 | 路由 14/14、计划 5/5 |

## 执行链路

```
用户问题
  → 意图路由（rag / tool / chat）
  → Skill 选择
  → 调工具（内部检索，或外部 MCP）
  → 外部工具：人工审批（暂停/恢复，超时 fail-closed）
  → 按输出契约组织答案
  → 失败则走兜底：拒答 / 报告错误 / 不伪造
```

## 可验证的数据

- 检索：61 题固定集，rerank 关→开 recall@1 0.59→1.0、MRR 0.772→1.0
- 生成：12 正 + 8 负，正确率 91.7%、引用正确率 100%、8/8 干净拒答
- 路由：14 题 14/14（规则分类，零 LLM 花费）
- 计划执行：5 个多步任务 5/5，平均 1.8 步 / 2877 token / 16.1s
- 工程：123 个测试、故障注入、跨租户对抗

## 代码位置

- Skill 本体：`.claude/skills/enterprise-kb-qa/SKILL.md`、`.claude/skills/warehouse-batch-status/SKILL.md`
- 演示脚本：各自 `scripts/` 目录（写操作只在 Skill 说明里走 `/agent/approve`，不提供绕过审批的脚本）
- 路由/审批/台账：`app/agent/router.py`、`app/agent/approval.py`、`app/agent/execution_ledger.py`
- 评测：`app/rag/*_eval.py`

## 边界（如实说明）

- 演示语料是项目自带的**虚构医药/仓储数据**，不涉及任何真实客户材料
- 如果贵司的 Skill 指平台化的技能配置（可视化编排），核心方法一致：触发条件、工具编排、输出契约、失败兜底，可按你们规范适配
