import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.guard import AgentRunLimitError, RunLimits, check_run

LIMITS = RunLimits(max_tool_steps=3, max_identical_calls=2, max_seconds=100, max_tokens=1000)


def _round(i: int, args: dict | None = None, tokens: int = 0) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": f"c{i}", "name": "echo", "args": args or {"x": i}}],
        usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": tokens},
    )


def test_under_limits_passes():
    messages = [HumanMessage("q"), _round(1), _round(2)]
    check_run(messages, time.monotonic(), LIMITS)


def test_tool_step_limit():
    messages = [HumanMessage("q"), _round(1), _round(2), _round(3)]
    with pytest.raises(AgentRunLimitError, match="步工具调用上限"):
        check_run(messages, time.monotonic(), LIMITS)


def test_identical_calls_detected():
    messages = [HumanMessage("q"), _round(1, {"x": "same"}), _round(2, {"x": "same"})]
    with pytest.raises(AgentRunLimitError, match="相同工具调用"):
        check_run(messages, time.monotonic(), LIMITS)


def test_token_budget():
    messages = [HumanMessage("q"), _round(1, tokens=1001)]
    with pytest.raises(AgentRunLimitError, match="token"):
        check_run(messages, time.monotonic(), LIMITS)


def test_wall_clock_budget():
    with pytest.raises(AgentRunLimitError, match="秒上限"):
        check_run([HumanMessage("q")], time.monotonic() - 101, LIMITS)


def test_previous_turns_do_not_count():
    history = [HumanMessage("old")] + [_round(i, {"x": i}) for i in range(5)]
    history.append(HumanMessage("new"))
    history.append(_round(99))
    check_run(history, time.monotonic(), LIMITS)
