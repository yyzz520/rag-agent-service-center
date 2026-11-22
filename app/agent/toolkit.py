"""工具目录：按路由/计划挑工具，工具实现本身不感知"为什么被选中"。

路由只决定"给模型看哪些工具"，权限（allowed_doc_ids）仍由检索工具闭包绑定，
两者不混：换路由不会放大可见范围。
"""
from app.agent.mcp_tools import get_mcp_tools
from app.agent.tools import calculator, make_search_knowledge_base_tool


def external_tools() -> list:
    return list(get_mcp_tools())


def tools_for_route(allowed_doc_ids: list[str] | None, route: str | None = None) -> list:
    search = make_search_knowledge_base_tool(allowed_doc_ids)
    mcp = external_tools()
    if route == "rag":
        return [search, calculator]
    if route == "tool":
        # 没有接外部工具时退回知识库，避免"路由到空工具集"
        return [calculator, *mcp] if mcp else [search, calculator]
    if route == "chat":
        return [calculator]
    return [search, calculator, *mcp]


def tool_map(allowed_doc_ids: list[str] | None) -> dict:
    """计划执行按名字取工具（计划里的工具必须在白名单里）。"""
    return {t.name: t for t in tools_for_route(allowed_doc_ids, None)}
