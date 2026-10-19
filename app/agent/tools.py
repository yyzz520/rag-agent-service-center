from langchain_core.tools import tool

from app.rag.retrieval import get_rerank_retriever


def make_search_knowledge_base_tool(allowed_doc_ids: list[str] | None):
    """按请求作用域造一个知识库检索工具。

    多租户隔离的关键：allowed_doc_ids 在造工具时闭包绑定，工具执行时直接用，
    不再靠 ContextVar 穿 async 流式 / 工具线程池（那会在边界上丢，6.9#5）。
    agent 每个请求都按自己范围 bind 一个，天然不串用户。

    同一轮里同一条 query 重复调用直接复用上次结果：模型偶尔会把一模一样的问题
    再问一遍（多轮 ReAct 里尤其常见），检索要跑改写+混合+rerank，是真金白银和延迟。
    seen 是闭包变量，工具每请求重建 → 去重范围天然就是"本次 agent 运行内"，
    不会跨请求/跨用户串结果。
    """

    seen: dict[str, str] = {}

    @tool("search_knowledge_base")
    def search_knowledge_base(query: str) -> str:
        """当用户问题涉及知识库/文档资料时调用：从知识库检索最相关的段落并返回。"""
        key = query.strip()
        if key in seen:
            return f"（重复查询，复用上次结果）\n{seen[key]}"
        docs = get_rerank_retriever(allowed_doc_ids=allowed_doc_ids).invoke(query)
        result = "\n\n---\n\n".join(d.page_content for d in docs) if docs else "知识库中没有检索到相关内容。"
        seen[key] = result
        return result

    return search_knowledge_base


# calculator 不做防重：纯计算、零成本、零副作用，重复算一次不花任何东西，
# 去重只是徒增一层状态。防重只值得花在有 API 成本/有副作用的工具上（这里就是检索）。
@tool
def calculator(expression: str) -> str:
    """计算数学表达式，例如 '3*(2+5)' 或 '2**10'。仅支持数字和 + - * / ** ( ) 空格。"""
    allowed = set("0123456789+-*/(). ")
    if not set(expression).issubset(allowed):
        return "包含非法字符，仅支持数字和 + - * / ( ) 空格"
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:
        return f"计算失败：{e}"
