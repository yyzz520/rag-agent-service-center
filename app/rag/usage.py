"""每 run LLM token / 步数记账（M3 待办落地）。

改造前只记了检索召回与最终答案；模型调用消耗多少 token、agent 走了几步工具完全黑盒。
这里给"一次问答"记账：入口 begin_trace 时在 ctx 里挂一个 UsageRecorder，各 LLM 调用点
把响应的 usage_metadata 记进去；log_answer 收尾时把汇总并进同 trace_id 的 answer 记录，
/trace 就能看到"这一问烧了多少 token、调了几次模型、跑了几步工具"。

为什么不靠回调/流式解析：RAG 的改写与回答、agent 内部多轮模型调用分布在几处，
串 config callbacks 传播链路脆弱；统一改从"看得见 AIMessage 的地方"直接读 usage_metadata，
每个调用点显式 record() 一次，行为可预期。

口径说明：
- 流式输出时 OpenAI 兼容接口在**最后一个 chunk** 带上本次调用的累计 usage_metadata，
  所以流式路径只在收尾记录、不会重复累计；
- 同一次请求内主供应商失败→备份重试时两次都会记——那本来就是真实消耗。
"""
from __future__ import annotations

_LEAF_KEYS = {
    "input_tokens": ("input_tokens", "prompt_tokens"),
    "output_tokens": ("output_tokens", "completion_tokens"),
    "total_tokens": ("total_tokens",),
}


def _pick(d: dict, keys: tuple[str, ...]):
    for k in keys:
        v = d.get(k)
        if v is not None:
            return int(v)
    return None


class UsageRecorder:
    """一次请求内累计 LLM 消耗。线程隔离靠 trace ctx 持有实例，不在此存全局。"""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.model_calls = 0
        self.tool_calls = 0

    def record(self, msg) -> None:
        """从 AIMessage 读 usage_metadata 并入账；没有则只计调用次数。"""
        um = getattr(msg, "usage_metadata", None)
        if um:
            self.record_usage_meta(um)
        else:
            self.model_calls += 1

    def record_usage_meta(self, um: dict) -> None:
        """流式收尾专用：把一次 LLM 调用的累计 usage dict 入账并计一次调用。"""
        self.prompt_tokens += _pick(um, _LEAF_KEYS["input_tokens"]) or 0
        self.completion_tokens += _pick(um, _LEAF_KEYS["output_tokens"]) or 0
        self.total_tokens += _pick(um, _LEAF_KEYS["total_tokens"]) or 0
        self.model_calls += 1

    def count_tool(self) -> None:
        self.tool_calls += 1

    def summary(self) -> dict | None:
        """有真实消耗才返回（避免给白跑的 eval / 空请求也记一行 0）。"""
        if not self.model_calls:
            return None
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }
