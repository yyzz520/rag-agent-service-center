from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from datetime import datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command

from app.agent import approval
from app.config import settings


@pytest.fixture(autouse=True)
def _temp_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_exec_ledger_path", str(tmp_path / "ledger.db"))


def _ai(calls: list[dict]) -> AIMessage:
    return AIMessage(content="", tool_calls=calls)


def test_expiry_and_reject_decisions():
    now = datetime.now(timezone.utc)
    fresh = {"created_at": now.isoformat(), "pending": [{"tool_call_id": "c1"}]}
    stale = {"created_at": (now - timedelta(seconds=999)).isoformat(), "pending": [{"tool_call_id": "c1"}]}
    assert not approval.is_expired(fresh, timeout=300)
    assert approval.is_expired(stale, timeout=300)
    assert approval.reject_decisions(stale) == {"c1": {"type": "reject"}}


def test_local_read_only_tools_skip_approval():
    assert not approval.needs_approval("search_knowledge_base")
    assert not approval.needs_approval("calculator")
    assert approval.needs_approval("mcp_wms_query")


def test_hook_returns_none_when_nothing_needs_approval(monkeypatch):
    def boom(_payload):
        raise AssertionError("不该触发 interrupt")

    monkeypatch.setattr(approval, "interrupt", boom)
    state = {"messages": [HumanMessage("q"), _ai([
        {"id": "c1", "name": "search_knowledge_base", "args": {"query": "a"}},
    ])]}
    assert approval.make_approval_hook()(state) is None


def test_hook_approves_edits_and_auto_passes_local(monkeypatch):
    monkeypatch.setattr(approval, "interrupt", lambda payload: {"decisions": {
        "c1": {"type": "approve"},
        "c2": {"type": "edit", "args": {"x": "edited"}},
    }})
    state = {"messages": [HumanMessage("q"), _ai([
        {"id": "c1", "name": "mcp_write", "args": {"x": "a"}},
        {"id": "c2", "name": "mcp_write", "args": {"x": "b"}},
        {"id": "c3", "name": "calculator", "args": {"expression": "1+1"}},
    ])]}

    out = approval.make_approval_hook()(state)

    assert isinstance(out["messages"][0], RemoveMessage)
    rewritten = out["messages"][1]
    assert isinstance(rewritten, AIMessage)
    assert [c["args"] for c in rewritten.tool_calls] == [{"x": "a"}, {"x": "edited"}, {"expression": "1+1"}]
    assert not any(isinstance(m, ToolMessage) for m in out["messages"])


def test_hook_skips_replay_of_executed_call(monkeypatch):
    monkeypatch.setattr(approval, "interrupt", lambda payload: {
        "decisions": {"c1": {"type": "approve"}},
    })
    state = {"messages": [HumanMessage("q"), _ai([
        {"id": "c1", "name": "mcp_write", "args": {"x": "a"}},
    ])]}
    hook = approval.make_approval_hook("scope-replay")

    assert hook(state) is None  # 首次：批准并落台账，工具交给 ToolNode 执行
    replay = hook(state)  # 模拟崩溃后重放同一状态

    tool_msgs = [m for m in replay["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert "此前已提交执行" in tool_msgs[0].content


def test_hook_missing_decision_fails_closed(monkeypatch):
    monkeypatch.setattr(approval, "interrupt", lambda payload: None)
    state = {"messages": [HumanMessage("q"), _ai([
        {"id": "c1", "name": "mcp_write", "args": {"x": "a"}},
    ])]}

    out = approval.make_approval_hook()(state)

    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1 and tool_msgs[0].tool_call_id == "c1"
    rewritten = [m for m in out["messages"] if isinstance(m, AIMessage)][0]
    assert rewritten.tool_calls == []


class ScriptedChatModel(BaseChatModel):
    responses: list
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _build(executed: list[str]):
    @tool("write_demo")
    def write_demo(x: str) -> str:
        """写操作演示"""
        executed.append(x)
        return f"wrote {x}"

    model = ScriptedChatModel(responses=[
        AIMessage(content="准备写入", tool_calls=[
            {"id": "c1", "name": "write_demo", "args": {"x": "a"}},
        ]),
        AIMessage(content="完成"),
    ])
    app = create_react_agent(
        model, [write_demo],
        post_model_hook=approval.make_approval_hook(),
        checkpointer=MemorySaver(),
    )
    return app, {"configurable": {"thread_id": "t"}}


def test_pauses_before_tool_then_resumes_on_approve(monkeypatch):
    monkeypatch.setattr(approval, "needs_approval", lambda name: name == "write_demo")
    executed: list[str] = []
    app, config = _build(executed)

    first = app.invoke({"messages": [HumanMessage("写 a")]}, config)

    assert first["__interrupt__"]
    assert executed == []
    pending = first["__interrupt__"][0].value["pending"]
    assert pending[0]["name"] == "write_demo"

    done = app.invoke(
        Command(resume={"decisions": {"c1": {"type": "approve"}}}), config
    )
    assert executed == ["a"]
    assert done["messages"][-1].content == "完成"


def test_reject_skips_tool_and_continues(monkeypatch):
    monkeypatch.setattr(approval, "needs_approval", lambda name: name == "write_demo")
    executed: list[str] = []
    app, config = _build(executed)
    app.invoke({"messages": [HumanMessage("写 a")]}, config)

    done = app.invoke(
        Command(resume={"decisions": {"c1": {"type": "reject"}}}), config
    )

    assert executed == []
    assert done["messages"][-1].content == "完成"
    rejected = [
        m for m in done["messages"]
        if isinstance(m, ToolMessage) and m.tool_call_id == "c1"
    ]
    assert rejected and rejected[0].additional_kwargs.get("approval") == "rejected"
