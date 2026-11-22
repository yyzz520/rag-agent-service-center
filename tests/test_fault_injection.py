import asyncio
import sqlite3

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent import approval, context_budget, mcp_tools
from app.rag import chain


def test_budget_save_failure_still_truncates(monkeypatch):
    monkeypatch.setattr(context_budget, "_save", lambda *args: None)
    monkeypatch.setattr(context_budget.settings, "agent_tool_result_max_chars", 100)
    monkeypatch.setattr(context_budget.settings, "agent_max_context_chars", 100000)
    msgs = [
        HumanMessage("q"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "big", "args": {}}]),
        ToolMessage(content="x" * 500, tool_call_id="c1", name="big", id="m1"),
    ]

    out = context_budget.apply_budget(msgs, "scope")

    new = [m for m in out["messages"] if isinstance(m, ToolMessage)][0]
    assert len(new.content) < 500
    assert "落盘失败" in new.content


def test_ledger_unavailable_fails_closed(monkeypatch):
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(approval.execution_ledger, "mark_executed", boom)
    monkeypatch.setattr(approval, "interrupt", lambda payload: {
        "decisions": {"c1": {"type": "approve"}},
    })
    state = {"messages": [HumanMessage("q"), AIMessage(content="", tool_calls=[
        {"id": "c1", "name": "mcp_write", "args": {"x": "a"}},
    ])]}

    out = approval.make_approval_hook("scope")(state)

    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs and tool_msgs[0].additional_kwargs["approval"] == "ledger_unavailable"
    assert "未执行" in tool_msgs[0].content


def test_llm_failure_propagates_without_fake_answer(monkeypatch):
    monkeypatch.setattr(chain, "retrieve_context", lambda question, allowed_doc_ids=None: {
        "context": "资料", "sources": [{"id": 1}],
    })

    from langchain_core.runnables import RunnableLambda

    def broken(_payload):
        raise RuntimeError("provider 500")

    with pytest.raises(RuntimeError, match="provider 500"):
        chain.run_rag("问题", llm=RunnableLambda(broken))


def test_mcp_tool_timeout_is_bounded():
    async def slow():
        await asyncio.sleep(1)

    with pytest.raises(TimeoutError):
        mcp_tools._run_coro(slow(), 0.05)
