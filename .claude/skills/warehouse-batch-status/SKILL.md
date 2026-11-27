---
name: warehouse-batch-status
description: 当用户询问某个批号的实时仓储状态（库区、温控、放行状态、结存数量、在库批次）时使用。
---

# 仓储批号状态查询

## 何时使用

- 用户问“批号 QS-xxx 在哪个库区”“是否放行”“结存多少”“现在有哪些在库批次”
- 问题需要调用外部仓储系统（MCP 工具），答案不在文档里

## 不适用

- 制度、SOP、温控标准等文档知识 → 用 `enterprise-kb-qa`
- 需要推断/合规判断时：先查本 Skill 拿实时数据，再查知识库拿标准，最后合成

## 输入

- `batch_no`：批号（单批次查询）
- 或“在库批次列表”类问题（调用 `list_batches`）
- 或“放行某批成品”（调用 `release_batch`，写操作，必须走人工审批）

## 执行步骤

1. 走 Agent 接口 `POST /api/v1/agent/chat`，body: `{"question": "..."}`；路由会把它分到 `tool`
2. 外部工具执行前会返回 `status=awaiting_approval` 和 `pending`（工具名+参数）
3. 人工审批：`POST /api/v1/agent/approve`
   - `approve`：执行工具
   - `reject`：不执行，模型如实说明无法查询
   - `edit`：修正参数（例如批号写错）后再执行
4. 工具返回后，按“库区 / 温控要求 / 放行状态 / 结存数量”组织答案
5. 数据缺失时明确说缺什么，不猜测温度或库存

> 只读示例可直连脚本：`python .claude/skills/warehouse-batch-status/scripts/query_batch.py QS-2024-9527`
> 线上必须走 `/agent/chat`，这样外部工具才会经过人工审批（HITL）与执行台账。

## 输出契约

```json
{
  "batch_no": "QS-2024-9527",
  "material_type": "成品",
  "zone": "冷藏库 B-03",
  "storage_condition": "2~8℃",
  "qa_status": "已放行",
  "batch_record_ok": true,
  "deviation_open": false,
  "expiry": "2027-06-30",
  "quantity": 1200
}
```

审批中：

```json
{"status": "awaiting_approval", "pending": [{"name": "get_batch_storage", "args": {"batch_no": "QS-2024-9527"}}]}
```

## 失败处理

- 审批拒绝：不执行工具，回答“工具执行未通过人工审批”，不编造数据
- 审批超时：默认按拒绝处理（fail-closed），并写审计
- MCP 超时/不可用：报告工具失败，不伪造空结果
- 参数校验失败：把错误反馈给用户，必要时用 `edit` 修正后重试
- 放行被拒：批记录不完整 / 存在未关闭偏差 / 状态不是待放行 —— 逐条说明原因，不放行

## 示例

问题：查一下批号 QS-2024-9527 现在在哪个库区、是否放行？
流程：`awaiting_approval` → approve → 工具返回“冷藏库 B-03 / 2~8℃ / 已放行 / 批记录完整 / 无未关闭偏差 / 1200 件” → 组织答案。

问题：把 QS-2024-6001 放行。
流程：`release_batch` 需审批 → approve → 状态变为已放行（写结果落 `data/wms_mock.json`）；再次放行会提示“已是已放行，未重复执行”。

## 评测依据

- 路由评测：14 题固定集 14/14
- 计划执行评测：5 个多步任务 5/5，平均 1.8 步 / 2877 token / 16.1s
- 合成数据为项目自带的虚构仓储数据，不涉及任何真实业务材料

## 相关实现

`app/agent/router.py`、`app/agent/approval.py`、`app/agent/execution_ledger.py`、`mcp_servers/wms_server.py`（假数据 `data/wms_mock.json`，删除即重置）
