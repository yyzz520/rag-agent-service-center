"""请求路由：按意图选工具集与策略。

先用规则（可解释、零成本、失败可回退默认），不默认调模型做分类——路由错一次
（该查外部系统却只搜文档）比多花一次分类 token 更伤。需要时可在 classify 外层
接小模型分类器，把 RouteDecision 当契约即可。
"""
from dataclasses import dataclass

from app.agent import toolkit

ROUTE_VERSION = "route.v1"

_TOOL_HINTS = (
    "批号", "库存", "仓库", "库区", "在库", "放行状态", "工单", "订单",
    "物流", "快递", "实时", "现在在", "系统里", "接口",
)
_RAG_HINTS = (
    "资料", "文档", "知识库", "合同", "条款", "制度", "手册", "规定",
    "要求", "标准", "是什么", "怎么", "多少", "根据", "流程",
)
_CHAT_HINTS = (
    "你好", "您好", "谢谢", "再见", "在吗", "你是谁", "hi", "hello", "thanks",
)

_ROUTE_PROMPTS = {
    "rag": "当前模式：知识库问答。涉及用户文档/资料时先用 search_knowledge_base 检索，再依据原文回答。",
    "tool": "当前模式：外部系统操作。实时状态（库存/批号/工单等）必须调用外部工具查询，不要凭知识库脑补。",
    "chat": "当前模式：普通对话。无需检索文档，直接简洁回答；确实需要上下文再调用工具。",
}


@dataclass(frozen=True)
class RouteDecision:
    route: str
    reason: str


def classify(question: str, has_external_tools: bool | None = None) -> RouteDecision:
    if has_external_tools is None:
        has_external_tools = bool(toolkit.external_tools())
    text = (question or "").strip().lower()
    if not text:
        return RouteDecision("rag", "empty")

    if any(hint in text for hint in _TOOL_HINTS):
        if has_external_tools:
            return RouteDecision("tool", "keyword_tool")
        return RouteDecision("rag", "no_external_tools")
    if any(hint in text for hint in _CHAT_HINTS) and not any(
        hint in text for hint in _RAG_HINTS
    ):
        return RouteDecision("chat", "greeting")
    if any(hint in text for hint in _RAG_HINTS):
        return RouteDecision("rag", "keyword_rag")
    return RouteDecision("rag", "default")


def route_suffix(route: str | None) -> str:
    return _ROUTE_PROMPTS.get(route or "", "")
