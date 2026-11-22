from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from app.agent.context_budget import apply_budget
from app.config import settings


def _tool(content: str, call_id: str, msg_id: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id, name="search_knowledge_base", id=msg_id)


def _messages(*tools: ToolMessage) -> list:
    calls = [{"id": t.tool_call_id, "name": "search_knowledge_base", "args": {}} for t in tools]
    return [HumanMessage("q"), AIMessage(content="", tool_calls=calls), *tools]


def test_long_result_is_saved_and_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_result_dir", str(tmp_path / "results"))
    monkeypatch.setattr(settings, "agent_tool_result_max_chars", 100)
    monkeypatch.setattr(settings, "agent_max_context_chars", 100000)

    out = apply_budget(_messages(_tool("x" * 500, "c1", "m1")), "scope1")

    assert isinstance(out["messages"][0], RemoveMessage)
    new = out["messages"][1]
    assert isinstance(new, ToolMessage) and new.id == "m1"
    assert len(new.content) < 500 and "已截断" in new.content
    saved = list((tmp_path / "results" / "scope1").glob("*.txt"))
    assert saved and saved[0].read_text() == "x" * 500


def test_small_results_untouched(monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_result_max_chars", 1000)
    monkeypatch.setattr(settings, "agent_max_context_chars", 100000)
    assert apply_budget(_messages(_tool("short", "c1", "m1")), "scope") is None


def test_total_budget_compresses_older_results(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_result_dir", str(tmp_path / "results"))
    monkeypatch.setattr(settings, "agent_tool_result_max_chars", 2000)
    monkeypatch.setattr(settings, "agent_max_context_chars", 4000)
    monkeypatch.setattr(settings, "agent_tool_result_keep_recent", 1)

    msgs = _messages(
        _tool("a" * 1500, "c1", "m1"),
        _tool("b" * 1500, "c2", "m2"),
        _tool("c" * 1500, "c3", "m3"),
    )
    out = apply_budget(msgs, "scope")
    replaced = {m.id: m for m in out["messages"] if isinstance(m, ToolMessage)}
    assert "已截断" in replaced["m1"].content
    assert "已截断" in replaced["m2"].content
    assert "m3" not in replaced  # 最近一条保留完整


class _ScriptedChatModel(BaseChatModel):
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


def test_budget_through_pre_model_hook_truncates_tool_result(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_result_dir", str(tmp_path / "results"))
    monkeypatch.setattr(settings, "agent_tool_result_max_chars", 200)
    monkeypatch.setattr(settings, "agent_max_context_chars", 100000)

    @tool("big_result")
    def big_result(x: str) -> str:
        """返回大结果"""
        return "y" * 5000

    model = _ScriptedChatModel(responses=[
        AIMessage(content="", tool_calls=[
            {"id": "c1", "name": "big_result", "args": {"x": "a"}},
        ]),
        AIMessage(content="完成"),
    ])
    app = create_react_agent(
        model, [big_result],
        pre_model_hook=lambda state: apply_budget(state["messages"], "scope"),
    )

    result = app.invoke({"messages": [HumanMessage("q")]})

    [tool_msg] = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msg.content) < 5000
    assert "已截断" in tool_msg.content


def test_budget_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "agent_tool_result_dir", str(tmp_path / "results"))
    monkeypatch.setattr(settings, "agent_tool_result_max_chars", 100)
    monkeypatch.setattr(settings, "agent_max_context_chars", 100000)

    msgs = _messages(_tool("x" * 500, "c1", "m1"))
    first = apply_budget(msgs, "scope")
    # 模拟第二遍：上一轮的替换结果已进 state
    new_msgs = [m for m in msgs if not isinstance(m, ToolMessage)]
    new_msgs.append(first["messages"][1])
    assert apply_budget(new_msgs, "scope") is None
