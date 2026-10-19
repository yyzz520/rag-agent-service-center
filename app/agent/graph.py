import logging
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import RemoveMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.prebuilt import create_react_agent

from app.agent.mcp_tools import get_mcp_tools
from app.agent.tools import calculator, make_search_knowledge_base_tool
from app.ai.providers import backup_llm, primary_llm
from app.config import settings
from app.rag.trace import current_usage

logger = logging.getLogger("app.agent.graph")

_AGENT_TEMPERATURE = 0.2


@lru_cache
def get_agent_llm():
    return primary_llm(_AGENT_TEMPERATURE)


@lru_cache
def get_agent_backup_llm():
    return backup_llm(_AGENT_TEMPERATURE)


def _checkpoint_db_path() -> Path:
    """sqlite 档的 checkpointer 落点：跟随业务库同文件；业务库非 sqlite 时单落一个文件。"""
    if settings.db_url.startswith("sqlite:///") and ":memory:" not in settings.db_url:
        return Path(settings.db_url.removeprefix("sqlite:///"))
    return Path("data/agent_checkpoints.db")


def _use_postgres_checkpointer() -> bool:
    """会话记忆走 Postgres 档：backend=postgres 且配了 dsn。"""
    return settings.checkpoint_backend == "postgres" and bool(settings.postgres_dsn)


def _sqlite_conn() -> sqlite3.Connection:
    path = _checkpoint_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 线程隔离：请求在线程池、删会话可能在别处，必须跨线程用同一连接
    return sqlite3.connect(str(path), check_same_thread=False)


@lru_cache
def get_checkpointer() -> SqliteSaver:
    """sqlite 档 checkpointer 单例（官方 langgraph-checkpoint-sqlite）。

    重启服务后旧 conversation_id 仍能续传记忆；仅供 sqlite 档使用，
    postgres 档走 open_sync_checkpointer()（每请求一个连接）。
    """
    saver = SqliteSaver(_sqlite_conn())
    saver.setup()
    return saver


@contextmanager
def open_sync_checkpointer():
    """同步 checkpointer 获取：sqlite 档复用进程内单例；postgres 档每请求开一个连接。

    取舍：postgres 用 PostgresSaver.from_conn_string（连接与事务由库内部治理），实现简单；
    高并发下应换 psycopg_pool.ConnectionPool 复用连接——见 README 部署说明。
    """
    if _use_postgres_checkpointer():
        from langgraph.checkpoint.postgres import PostgresSaver

        with PostgresSaver.from_conn_string(settings.postgres_dsn) as saver:
            yield saver
    else:
        yield get_checkpointer()


def delete_checkpoint_thread(thread_id: str) -> None:
    """删会话时把 checkpointer 里的消息一起清；失败不阻塞删会话行。"""
    try:
        with open_sync_checkpointer() as saver:
            saver.delete_thread(thread_id)
    except Exception:
        pass


def init_checkpointer() -> None:
    """启动时建表（幂等，可反复调）。postgres 档需显式 setup()；sqlite 档已含在 get_checkpointer()。"""
    if _use_postgres_checkpointer():
        from langgraph.checkpoint.postgres import PostgresSaver

        with PostgresSaver.from_conn_string(settings.postgres_dsn) as saver:
            saver.setup()
    else:
        get_checkpointer()


# 系统提示词：约束"调知识库工具时以文档原文为准"，和 /rag/chat 的防幻觉口径一致。
# 只在涉及文档时收紧，不拦计算器/闲聊类问题。
_AGENT_SYSTEM = (
    "你是一个能调用工具的智能助手。涉及用户文档/资料的问题，你会用 search_knowledge_base 检索。"
    "该工具返回的是用户上传文档的原文片段，回答必须以其为准、直接引用文档内容，"
    "不要凭自己的知识脑补文档里没有的内容；若工具返回『知识库中没有检索到相关内容』，"
    "就如实告诉用户知识库里没有，不要编造。回答直接、简洁：文档里明确写了什么就答什么，"
    "不要附加『可能因合同期限/地区/政策而有所不同』『请以实际合同为准』这类免责说明。"
    "计算器等其他工具按正常方式使用。"
)


def _scope_tools(allowed_doc_ids: list[str] | None):
    # 外部 MCP 工具（配置了才非空）追加在末尾：本地工具恒定存在，模型先看到"确定有"的能力
    return [make_search_knowledge_base_tool(allowed_doc_ids), calculator, *get_mcp_tools()]


def build_agent(
    allowed_doc_ids: list[str] | None = None,
    *,
    checkpointer=None,
    llm=None,
):
    """按请求作用域现拼一个 agent（每请求一个，工具闭包绑定 allowed_doc_ids，隔离不靠 ContextVar）。

    每请求重新 create_react_agent 只是编译开销（毫秒级）；换来的是作用域不可能串用户。
    checkpointer 默认用同步 sqlite 单例；流式接口传 AsyncSqliteSaver（async astream 必需）。
    llm 默认主供应商；fallback 时传备份 LLM 重建 agent（见路由层的"主挂→备份重试"）。
    """
    if checkpointer is None:
        if _use_postgres_checkpointer():
            # 不静默退回 sqlite：postgres 档下没传 saver 多半是漏了 open_sync_checkpointer()
            raise RuntimeError(
                "postgres 档必须显式传入 checkpointer（用 open_sync_checkpointer() 获取）"
            )
        checkpointer = get_checkpointer()
    return create_react_agent(
        llm or get_agent_llm(),
        _scope_tools(allowed_doc_ids),
        prompt=_AGENT_SYSTEM,
        checkpointer=checkpointer,
    )


@asynccontextmanager
async def open_async_checkpointer():
    """SSE 流式用：不能复用同步 saver（astream 要求 async checkpointer）。

    sqlite 档连同一 sqlite 文件；postgres 档连同一 dsn——两条路径的会话记忆都跨
    invoke/astream 共享，切换 backend 只改配置不动这里。
    """
    if _use_postgres_checkpointer():
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(settings.postgres_dsn) as saver:
            yield saver
    else:
        async with AsyncSqliteSaver.from_conn_string(str(_checkpoint_db_path())) as saver:
            yield saver


# ---- 长会话上下文压缩（37）----
# 思路：turn 开始前读线程历史，超阈值就把更早消息折成一条摘要 SystemMessage 放队首、
# 只留最近几条原文（LangGraph 官方 summarization 的 RemoveMessage + update_state 方案，
# 不用改 create_react_agent 的 state schema，prebuilt 原样可用）。

_SUMMARY_SYSTEM = (
    "你负责把一段智能助手与用户的对话浓缩成中文摘要，供后续对话作背景记忆。"
    "要点是尽量保真、别丢信息：用户提到的专名/名称、数字、颜色、偏好等具体事实必须原样保留，"
    "宁可多列几条也别省略；同时概括未完成的任务与已给的结论。直接输出摘要本身，不要『以下是摘要』这类前缀。"
)

_ROLE_LABEL = {"human": "用户", "ai": "助手", "tool": "工具结果", "system": "系统"}


def _summary_prompt(history: list) -> list[dict]:
    lines = []
    for m in history:
        role = _ROLE_LABEL.get(getattr(m, "type", ""), getattr(m, "type", "消息"))
        content = getattr(m, "content", "")
        if not isinstance(content, str):
            content = str(content)
        lines.append(f"[{role}] {content}")
    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": "以下是待压缩的历史对话：\n\n" + "\n".join(lines)},
    ]


def _summarize(history: list, llm) -> str:
    """压缩摘要也是一次真实 LLM 调用，记进当次 usage（同步/异步共用 llm.invoke 的封装）。"""
    llm = llm or get_agent_llm()
    resp = llm.invoke(_summary_prompt(history))
    rec = current_usage()
    if rec is not None:
        rec.record(resp)
    content = resp.content
    return content if isinstance(content, str) else str(content)


async def _asummarize(history: list, llm) -> str:
    llm = llm or get_agent_llm()
    resp = await llm.ainvoke(_summary_prompt(history))
    rec = current_usage()
    if rec is not None:
        rec.record(resp)
    content = resp.content
    return content if isinstance(content, str) else str(content)


def _partition(msgs: list, keep: int) -> tuple[list, list]:
    """切成 (待压缩 old, 保留 recent)。recent 不以 tool 消息开头：否则它的发起 ai 落在
    old 被删，留下悬空的 tool_call_id（OpenAI 兼容接口会拒掉这类消息）。"""
    i = max(0, len(msgs) - keep)
    while i > 0 and getattr(msgs[i], "type", "") == "tool":
        i -= 1
    if i <= 0:
        return [], msgs  # 压缩区为空就没必要压了
    return msgs[:i], msgs[i:]


_SUMMARY_ID = "__ctx_summary__"  # 摘要固定 id：下一轮压缩能精确删掉旧摘要，不产生重复


def _fresh_new_id(msg):
    """换新顶层 id 的深拷贝：add_messages 对"重加同 id 消息"是原位替换（recent 会被钉在
    原位、摘要垫到末尾）；给 recent 换**全新随机 id** 后 reducer 严格按列表顺序追加，才能得到
    [摘要, *recent] 的正确时序。tool_calls/tool_call_id 内部结构原样保留，关联不丢。

    为什么换新 id 而不是置空：置空会留下无法被 RemoveMessage 定位的无 id 消息，
    线程只能压缩一轮、此后 _compress_needed 因"有消息无 id"永久跳过；新 id 既保证
    追加顺序，又让下一轮仍能精确删除，压缩可持续循环。"""
    try:
        return msg.model_copy(update={"id": uuid.uuid4().hex})
    except Exception:
        return msg


def _compress_state_update(all_msgs: list, recent: list, summary_text: str) -> dict:
    """全删重建：删掉全部旧消息（含上一轮摘要），再按序放入
    [摘要 SystemMessage(固定 id), *换新 id 的 recent]，保证摘要排队首且可重复压缩。"""
    return {
        "messages": [
            *[RemoveMessage(id=m.id) for m in all_msgs if getattr(m, "id", None)],
            SystemMessage(content=f"【更早对话摘要】{summary_text}", id=_SUMMARY_ID),
            *[_fresh_new_id(m) for m in recent],
        ]
    }


def _compress_needed(msgs: list) -> tuple[list, list] | None:
    """低于阈值 / 消息没 id（无法精确删除）→ None，跳过本轮压缩。"""
    if len(msgs) <= settings.compress_min_messages:
        return None
    if any(not getattr(m, "id", None) for m in msgs):
        logger.debug("线程消息缺少 id，跳过压缩")
        return None
    keep = max(1, settings.compress_keep_recent)
    old, recent = _partition(msgs, keep)
    return (old, recent) if old else None


def maybe_compress_thread(agent, conversation_id: str, llm=None) -> None:
    """同步入口（非流式 invoke 前）。压缩是尽力而为：摘要失败只告警，不阻塞本轮回答。"""
    config = {"configurable": {"thread_id": conversation_id}}
    snap = agent.get_state(config)
    msgs = list((snap.values or {}).get("messages") or [])
    cut = _compress_needed(msgs)
    if cut is None:
        return
    old, recent = cut
    try:
        text = _summarize(old, llm)
    except Exception as e:
        logger.warning("历史摘要失败，跳过本轮压缩（question 不受影响）：%s", e)
        return
    agent.update_state(config, _compress_state_update(msgs, recent, text))
    logger.info("线程 %s 压缩：%d 条历史 → 摘要 + %d 条原文", conversation_id[:8], len(msgs), len(recent))


async def amaybe_compress_thread(agent, conversation_id: str, llm=None) -> None:
    """异步入口（SSE astream 前）。AsyncSqliteSaver 需走 aget_state/aupdate_state。"""
    config = {"configurable": {"thread_id": conversation_id}}
    snap = await agent.aget_state(config)
    msgs = list((snap.values or {}).get("messages") or [])
    cut = _compress_needed(msgs)
    if cut is None:
        return
    old, recent = cut
    try:
        text = await _asummarize(old, llm)
    except Exception as e:
        logger.warning("历史摘要失败，跳过本轮压缩（question 不受影响）：%s", e)
        return
    await agent.aupdate_state(config, _compress_state_update(msgs, recent, text))
    logger.info("线程 %s 压缩：%d 条历史 → 摘要 + %d 条原文", conversation_id[:8], len(msgs), len(recent))
