import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agent import planner


class ScriptedChatModel(BaseChatModel):
    responses: list
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _plan(*steps) -> AIMessage:
    return AIMessage(content=json.dumps({"steps": list(steps)}, ensure_ascii=False))


def _make_agent(model):
    executed: list[str] = []

    @tool("fake_storage")
    def fake_storage(batch_no: str) -> str:
        """查询批号的库区与状态。"""
        executed.append(batch_no)
        return f"批号 {batch_no}：冷藏库 B-03，已放行"

    app = planner.build_plan_agent(
        None,
        checkpointer=MemorySaver(),
        llm=model,
        conversation_id="c1",
        tool_registry={"fake_storage": fake_storage},
    )
    return app, executed, {"configurable": {"thread_id": "c1"}}


def test_plan_pauses_then_approve_executes_steps():
    model = ScriptedChatModel(responses=[
        _plan({"goal": "查批号状态", "tool": "fake_storage", "args": {"batch_no": "QS-1"}}),
        AIMessage(content="批号 QS-1 在冷藏库 B-03，已放行。"),
    ])
    app, executed, config = _make_agent(model)

    first = app.invoke({"messages": [HumanMessage("查批号 QS-1 的库区状态")]}, config)

    assert first["__interrupt__"]
    payload = first["__interrupt__"][0].value
    assert payload["kind"] == "plan_approval"
    assert payload["steps"][0]["tool"] == "fake_storage"
    assert executed == []

    done = app.invoke(Command(resume={"type": "approve"}), config)
    assert executed == ["QS-1"]
    assert done["messages"][-1].content.startswith("批号 QS-1")


def test_reject_executes_nothing():
    model = ScriptedChatModel(responses=[
        _plan({"goal": "查批号", "tool": "fake_storage", "args": {"batch_no": "QS-1"}}),
        AIMessage(content="不应被调用"),
    ])
    app, executed, config = _make_agent(model)
    app.invoke({"messages": [HumanMessage("查批号 QS-1")]}, config)

    done = app.invoke(Command(resume={"type": "reject"}), config)

    assert executed == []
    assert "未获批准" in done["messages"][-1].content


def test_edit_plan_changes_args():
    model = ScriptedChatModel(responses=[
        _plan({"goal": "查批号", "tool": "fake_storage", "args": {"batch_no": "QS-1"}}),
        AIMessage(content="完成"),
    ])
    app, executed, config = _make_agent(model)
    app.invoke({"messages": [HumanMessage("查批号 QS-1")]}, config)

    edited = {"steps": [
        {"goal": "改查 QS-2", "tool": "fake_storage", "args": {"batch_no": "QS-2"}},
    ]}
    app.invoke(Command(resume={"type": "edit", "steps": edited["steps"]}), config)

    assert executed == ["QS-2"]


def test_invalid_plan_does_not_interrupt():
    model = ScriptedChatModel(responses=[AIMessage(content="这不是 JSON")])
    app, executed, config = _make_agent(model)

    result = app.invoke({"messages": [HumanMessage("随便问")]}, config)

    assert "__interrupt__" not in result
    assert "无法生成可执行计划" in result["messages"][-1].content
    assert executed == []


def test_plan_prompt_includes_arg_schema():
    @tool("schema_tool")
    def schema_tool(batch_no: str) -> str:
        """查询批号。"""
        return batch_no

    class CaptureLLM:
        def __init__(self):
            self.prompts = []

        def invoke(self, prompt):
            self.prompts.append(prompt)
            return AIMessage(content='{"steps": []}')

    llm = CaptureLLM()
    planner.generate_plan("查批号", {"schema_tool": schema_tool}, llm)

    assert "batch_no" in llm.prompts[0]
    assert "JSON Schema" in llm.prompts[0]


def test_validate_plan_drops_unknown_and_duplicate_steps():
    tools = {"fake_storage": object()}
    steps = [
        {"goal": "a", "tool": "fake_storage", "args": {"batch_no": "QS-1"}},
        {"goal": "dup", "tool": "fake_storage", "args": {"batch_no": "QS-1"}},
        {"goal": "bad", "tool": "missing_tool", "args": {}},
        {"goal": "b", "tool": "fake_storage", "args": {"batch_no": "QS-2"}},
    ]
    out = planner._validate_plan(steps, tools)
    assert [s["args"]["batch_no"] for s in out] == ["QS-1", "QS-2"]
