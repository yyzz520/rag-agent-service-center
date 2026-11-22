"""Human-in-the-loop：外部工具执行前暂停，等人审批后从 checkpoint 续跑。

审批放在 harness 层（模型输出之后、工具执行之前），不改工具实现：工具只描述"能做什么"，
"这次允不允许做"是运行时策略。本地只读工具（知识库检索/计算器）自动放行；MCP 等外部/
未知工具默认都要审批（fail-closed）。

暂停用 LangGraph `interrupt()`，状态由 checkpointer 落盘，所以恢复不依赖进程内存——
多副本、重启后都能用同一个 thread_id 续跑，审批结果由 /agent/approve 以
`Command(resume=...)` 送回。
"""
import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage
from langgraph.types import interrupt

from app.agent import execution_ledger
from app.config import settings

logger = logging.getLogger("app.agent.approval")

# 本地内置工具都是只读/零副作用，审批它们只会把产品体验拖垮
LOCAL_READ_ONLY_TOOLS = frozenset({"search_knowledge_base", "calculator"})

_REJECT_CONTENT = "人工审批未通过，该工具未执行。请基于已有信息继续，或说明无法完成。"
_REPEAT_CONTENT = "该工具调用此前已提交执行（幂等保护），本次不再重复执行；请人工复核上一次执行结果。"


class AgentAwaitingApproval(Exception):
    """本轮在工具执行前暂停，等待审批；payload 即 interrupt 的值。"""

    def __init__(self, payload: dict):
        super().__init__("agent 等待人工审批")
        self.payload = payload


def needs_approval(tool_name: str) -> bool:
    """外部/未知工具默认要审批；本地只读工具放行；总开关可关（关掉即全放行）。"""
    if not settings.agent_approval_enabled:
        return False
    return tool_name not in LOCAL_READ_ONLY_TOOLS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark_executed(scope: str, call: dict) -> bool:
    """执行前落账；返回 False = 同一 tool_call_id 已执行过（重放），本次应跳过。"""
    return execution_ledger.mark_executed(
        scope,
        call["id"],
        call.get("name", ""),
        json.dumps(call.get("args") or {}, ensure_ascii=False, sort_keys=True),
    )


def make_approval_hook(scope: str = "no-scope"):
    """post_model_hook：模型刚产出 tool_calls、工具还没执行——这是唯一的自然审批点。

    审批通过的危险工具在执行前落执行台账（at-most-once）；崩溃/重放时同一
    tool_call_id 不再执行，避免重复副作用。
    """

    def hook(state):
        messages = state.get("messages") or []
        last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if last_ai is None or not getattr(last_ai, "tool_calls", None):
            return None

        answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
        pending = [
            c for c in last_ai.tool_calls
            if needs_approval(c.get("name", "")) and c.get("id") not in answered
        ]
        if not pending:
            return None

        # 第一次到这：挂起；审批后节点重跑，interrupt() 直接返回 resume 值
        resume = interrupt({
            "kind": "tool_approval",
            "created_at": _now_iso(),
            "expires_in": settings.agent_approval_timeout,
            "pending": [
                {"tool_call_id": c["id"], "name": c.get("name"), "args": c.get("args") or {}}
                for c in pending
            ],
        })
        by_id = resume.get("decisions", resume) if isinstance(resume, dict) else {}

        updates: list = []
        new_calls: list[dict] = []
        for call in last_ai.tool_calls:
            if not needs_approval(call.get("name", "")):
                new_calls.append(call)
                continue
            decision = (by_id.get(call["id"]) or {}).get("type")
            edit_args = (by_id.get(call["id"]) or {}).get("args")
            if decision == "edit" and not isinstance(edit_args, dict):
                decision = "reject"  # 说改参数却没给参数，按拒绝兜底
            executed = False
            if decision == "approve":
                executed = _mark_executed(scope, call)
                if executed:
                    new_calls.append(call)
            elif decision == "edit":
                edited = {**call, "args": edit_args}
                executed = _mark_executed(scope, edited)
                if executed:
                    new_calls.append(edited)
            if decision in ("approve", "edit") and not executed:
                # 同一 tool_call_id 已执行过（崩溃/重放）：at-most-once，不重复副作用
                updates.append(ToolMessage(
                    content=_REPEAT_CONTENT,
                    tool_call_id=call["id"],
                    name=call.get("name"),
                ))
            elif decision not in ("approve", "edit"):
                # 缺失/超时/拒绝一律按拒绝处理（fail-closed），把结果作为工具消息回给模型
                updates.append(ToolMessage(
                    content=_REJECT_CONTENT,
                    tool_call_id=call["id"],
                    name=call.get("name"),
                ))

        if new_calls != last_ai.tool_calls:
            updates = [
                RemoveMessage(id=last_ai.id),
                AIMessage(content=last_ai.content, tool_calls=new_calls, id=last_ai.id),
                *updates,
            ]
        return {"messages": updates} if updates else None

    return hook
