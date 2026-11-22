"""ReAct 运行护栏：业务步数 / 重复调用 / 耗时 / token 四条硬上限。

`recursion_limit` 数的是 LangGraph super-step（一次模型+工具约 2 步），和业务口径不是一回事。
这里在 pre_model_hook 里按"模型调一轮工具算一步"熔断，超限抛异常终止本轮——不靠 prompt
劝模型停。抛出的 AgentRunLimitError 路由必须直接返回错误、不能走 fallback 重试。
"""
import json
import time
from dataclasses import dataclass

from langchain_core.messages import AIMessage

from app.config import settings


class AgentRunLimitError(RuntimeError):
    """本轮 agent 越过硬上限，已终止（路由返回 429，不触发供应商 fallback）。"""


@dataclass(frozen=True)
class RunLimits:
    max_tool_steps: int
    max_identical_calls: int
    max_seconds: float
    max_tokens: int


def default_limits() -> RunLimits:
    return RunLimits(
        max_tool_steps=settings.agent_max_tool_steps,
        max_identical_calls=settings.agent_max_identical_calls,
        max_seconds=settings.agent_max_run_seconds,
        max_tokens=settings.agent_max_run_tokens,
    )


def _signature(calls: list[dict]) -> tuple:
    """一轮工具调用的指纹：工具名 + 参数（排序后序列化），用于识别重复循环。"""
    return tuple(sorted(
        (c.get("name", ""), json.dumps(c.get("args") or {}, sort_keys=True, ensure_ascii=False))
        for c in calls
    ))


def _token_total(messages: list) -> int:
    total = 0
    for m in messages:
        if isinstance(m, AIMessage):
            um = getattr(m, "usage_metadata", None) or {}
            total += int(um.get("total_tokens") or 0)
    return total


def _current_turn(messages: list) -> list:
    """只取本轮（最后一条 human 之后）的消息：长会话的历史步数/token 不该把新一轮卡死。"""
    for i in range(len(messages) - 1, -1, -1):
        if getattr(messages[i], "type", "") == "human":
            return messages[i:]
    return messages


def check_run(messages: list, started_at: float, limits: RunLimits | None = None) -> None:
    """每次模型调用前检查；正常返回，越限抛 AgentRunLimitError。"""
    limits = limits or default_limits()
    elapsed = time.monotonic() - started_at
    if elapsed > limits.max_seconds:
        raise AgentRunLimitError(f"本次运行超过 {limits.max_seconds:.0f} 秒上限，已终止")

    messages = _current_turn(messages)
    rounds = [m for m in messages if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)]
    if len(rounds) >= limits.max_tool_steps:
        raise AgentRunLimitError(f"本次运行达到 {limits.max_tool_steps} 步工具调用上限，已终止")

    sigs = [_signature(m.tool_calls) for m in rounds]
    streak = 1
    for i in range(len(sigs) - 1, 0, -1):
        if sigs[i] != sigs[i - 1]:
            break
        streak += 1
    if streak >= limits.max_identical_calls:
        raise AgentRunLimitError(f"检测到连续 {streak} 轮相同工具调用，疑似死循环，已终止")

    tokens = _token_total(messages)
    if tokens > limits.max_tokens:
        raise AgentRunLimitError(f"本次运行 token 超过 {limits.max_tokens} 上限，已终止")


def make_pre_model_hook(limits: RunLimits | None = None):
    """在每次模型调用前检查；started_at 按"一轮用户请求"计时（审批恢复会重新计时）。"""
    started_at = time.monotonic()

    def hook(state):
        check_run(state.get("messages") or [], started_at, limits)
        return None

    return hook
