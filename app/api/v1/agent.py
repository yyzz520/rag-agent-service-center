import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.graph import (
    amaybe_compress_thread,
    build_agent,
    get_agent_backup_llm,
    maybe_compress_thread,
    open_async_checkpointer,
    open_sync_checkpointer,
)

logger = logging.getLogger("app.api.agent")
from app.api.responses import error, ok
from app.api.sse import sse_event
from app.config import settings
from app.core.security import UserContext, get_current_user
from app.db import get_db
from app.models import Session as ChatSession
from app.rag.trace import begin_trace, current_usage, end_trace, log_answer
from app.services.access import owned_doc, owned_doc_ids

router = APIRouter(prefix="/agent", tags=["智能体 Agent"])


class AgentRequest(BaseModel):
    question: str
    conversation_id: str | None = None  # GET /sessions 的 conversation_id；不传 = 新开会话
    doc: str | None = None  # 文档 ID（/docs 的 doc_id）；指定后知识库工具只在该文档内检索，null=本人全库


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _resolve_conversation(
    db: Session, user: UserContext, conversation_id: str | None, question: str
) -> tuple[str | None, ChatSession | None, object | None]:
    """会话归属：复用必须是我自己的；新建先落库拿 conversation_id（=session.id）。"""
    conv = (conversation_id or "").strip()
    if conv:
        row = db.execute(
            select(ChatSession).where(
                ChatSession.owner_id == user.user_id, ChatSession.id == conv
            )
        ).scalar_one_or_none()
        if row is None:
            return None, None, error(
                40002, "会话不存在或无权访问（conversation_id 需来自本人的 GET /sessions）"
            )
        return conv, row, None
    conv = uuid.uuid4().hex
    row = ChatSession(id=conv, owner_id=user.user_id, title=question[:50])
    db.add(row)
    db.commit()
    return conv, row, None


def _resolve_scope(
    db: Session, user: UserContext, doc_id: str | None
) -> tuple[list[str] | None, object | None]:
    """把 doc 参数收窄成"本人可检索文档 uuid 列表"：指定的必须属本人，否则 40002。"""
    if doc_id:
        doc = owned_doc(db, user.user_id, doc_id)
        if doc is None:
            return None, error(40002, "文档不存在或无权访问")
        return [doc.id], None
    return owned_doc_ids(db, user.user_id), None


def _agent_config(conversation_id: str) -> dict:
    # thread_id 是会话续传键；recursion_limit 显式传（6.10），别默默吃 LangGraph 默认 25
    return {
        "configurable": {"thread_id": conversation_id},
        "recursion_limit": settings.agent_recursion_limit,
    }


def _thread_has_pending_question(saver, conversation_id: str, question: str) -> bool:
    """重试前判断这轮 human 是否已落进线程（主供应商在模型节点挂：__start__ 已先入库）。

    真则"空输入续跑"（重放模型节点，不再补一条 human，避免重复提问）；假则正常带问题重跑。
    """
    tup = saver.get_tuple(_agent_config(conversation_id))
    msgs = (tup.checkpoint or {}).get("channel_values", {}).get("messages", []) if tup else []
    last = msgs[-1] if msgs else None
    return bool(last and getattr(last, "type", "") == "human" and str(last.content) == question)


def _invoke_agent(
    allowed_doc_ids: list[str] | None,
    conversation_id: str,
    question: str,
    llm=None,
    replay: bool = False,
    replay_check: bool = False,
) -> tuple[str, list[str]]:
    """跑一轮对话，返回 (answer, tools_used)。llm 默认主供应商；fallback 时传备份 LLM。

    replay=True：不重复塞 human，只续跑线程里已 checkpoint 的模型节点。
    replay_check=True：由本函数用同一个 saver 判断是否该续跑（fallback 重试用，
    省去在调用方另开一次 checkpointer —— postgres 档下每开一次就是一个新连接）。
    saver 由 open_sync_checkpointer() 提供：sqlite 档是进程内单例，postgres 档是本轮连接。
    """
    with open_sync_checkpointer() as saver:
        if replay_check and not replay:
            replay = _thread_has_pending_question(saver, conversation_id, question)
        agent = build_agent(allowed_doc_ids, checkpointer=saver, llm=llm)
        # 长会话压缩（37）：新的一轮开始前若线程历史超阈值，先把更早消息折成摘要（replay 不重复压）
        if not replay:
            maybe_compress_thread(agent, conversation_id, llm=llm)
        config = _agent_config(conversation_id)
        # agent.invoke 返回的是整条线程历史：usage 记账与 tools 列表都必须是"本轮"口径，
        # 不能把前几轮的 ai 消耗/工具再数一遍（多轮下会逐轮虚高/重复）。先快照线程长度，
        # invoke 后只统计尾部新追加的消息。
        snap = agent.get_state(config)
        prior_len = len(list((snap.values or {}).get("messages") or []))
        inputs = {} if replay else {"messages": [HumanMessage(content=question)]}
        result = agent.invoke(inputs, config=config)
        messages = result["messages"]
        answer = messages[-1].content
        new_msgs = messages[prior_len:]
        tools_used = [m.name for m in new_msgs if getattr(m, "type", "") == "tool"]
        _count_usage_from_messages(new_msgs, tool_count=len(tools_used))
    return answer, tools_used


def _count_usage_from_messages(messages: list, tool_count: int) -> None:
    """非流式：只统计本轮新追加的消息段（_invoke_agent 已切成 new_msgs 再传入）。

    AIMessage 带 usage_metadata（每次模型调用一条）；工具消息条数 = 本 run 实际执行工具数。
    """
    rec = current_usage()
    if rec is None:
        return
    for m in messages:
        if getattr(m, "type", "") == "ai" and getattr(m, "usage_metadata", None):
            rec.record(m)
    rec.tool_calls = max(rec.tool_calls, tool_count)


@router.post("/chat", summary="智能体对话（持久化会话记忆，仅限本人文档与会话）")
def agent_chat(
    req: AgentRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    question = req.question.strip()
    if not question:
        return error(40001, "question 不能为空")
    allowed, err = _resolve_scope(db, user, req.doc)
    if err is not None:
        return err
    conversation_id, session_row, err = _resolve_conversation(db, user, req.conversation_id, question)
    if err is not None:
        return err
    token = begin_trace(source="agent", conversation_id=conversation_id)
    try:
        try:
            answer, tools_used = _invoke_agent(allowed, conversation_id, question)
        except Exception:
            backup = get_agent_backup_llm()
            if backup is None:
                raise  # 没配备份供应商：直接按失败处理
            logger.warning("主供应商生成失败，回退备份供应商重试（agent，question=%r）", question[:50])
            # 主供应商失败后 human 通常已在 checkpointer 里：续跑而不是再塞一条，避免重复提问
            answer, tools_used = _invoke_agent(
                allowed,
                conversation_id,
                question,
                llm=backup,
                replay_check=True,
            )
        log_answer(question, answer)
        session_row.last_active_at = _now_utc()
        db.commit()
        return ok({
            "conversation_id": conversation_id,
            "doc": req.doc,
            "question": question,
            "answer": answer,
            "tools": tools_used,
        })
    except Exception as e:
        return error(50001, f"模型调用失败：{e}", status=500)
    finally:
        end_trace(token)


@router.post("/chat/stream", summary="智能体流式对话（SSE）：meta → content(逐字)/tool_start/tool_end → done / error")
def agent_chat_stream(
    req: AgentRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    question = req.question.strip()
    if not question:
        return error(40001, "question 不能为空")
    allowed, err = _resolve_scope(db, user, req.doc)
    if err is not None:
        return err
    conversation_id, session_row, err = _resolve_conversation(db, user, req.conversation_id, question)
    if err is not None:
        return err
    trace_id = uuid.uuid4().hex[:12]
    return StreamingResponse(
        _agent_stream(question, allowed, conversation_id, session_row, db, trace_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_agent_turn(
    saver,
    allowed_doc_ids: list[str] | None,
    conversation_id: str,
    question: str,
    answer_buf: list[str],
    tools_used: list[str],
    llm=None,
    replay: bool = False,
):
    """把一轮 astream（模型↔工具多步）解包成 content/tool_start/tool_end 事件。

    产出的事件回填到 answer_buf/tools_used（调用方持有，跨可能的重试复用）。
    llm 传备份供应商时重建 agent，checkpointer 复用同一个 saver，会话记忆不丢。
    replay=True：不重复塞 human，续跑线程里已 checkpoint 的模型节点（fallback 用，见下方重试）。
    """
    agent = build_agent(allowed_doc_ids, checkpointer=saver, llm=llm)
    # 长会话压缩（37）：astream 前的同步动作也走压缩，AsyncSqliteSaver 用 aget_state/aupdate_state
    if not replay:
        await amaybe_compress_thread(agent, conversation_id, llm=llm)
    inputs = {} if replay else {"messages": [HumanMessage(content=question)]}
    started_calls: set[int] = set()
    last_tool_end: tuple | None = None
    usage_rec = current_usage()
    pending_um: dict | None = None  # 当前这段 agent 模型流内最新(即累计)usage，撞工具/流末才入账
    # stream_mode="messages"：模型文本与工具执行逐条吐；用 langgraph_node 区分是 agent 在说
    # 还是在跑工具，分别路由成 content 事件和 tool_start/tool_end 事件。
    async for msg, meta in agent.astream(
        inputs,
        config=_agent_config(conversation_id),
        stream_mode="messages",
    ):
        node = (meta or {}).get("langgraph_node")
        if node == "agent":
            text = getattr(msg, "content", "")
            if text:
                answer_buf.append(text if isinstance(text, str) else str(text))
                yield sse_event("content", {"delta": text})
            for tcc in getattr(msg, "tool_call_chunks", None) or []:
                idx = tcc.get("index")
                name = tcc.get("name")
                if name and idx is not None and idx not in started_calls:
                    started_calls.add(idx)
                    tools_used.append(name)
                    yield sse_event("tool_start", {"name": name})
            # 每个 chunk 都带该次模型调用的累计 usage：只覆盖不累加，段末一次入账
            um = getattr(msg, "usage_metadata", None)
            if um:
                pending_um = um
        elif node == "tools":
            # 一段模型调用在工具结果前结束：把刚才攒的这次调用 usage 落账
            if pending_um is not None and usage_rec is not None:
                usage_rec.record_usage_meta(pending_um)
            pending_um = None
            name = getattr(msg, "name", None) or "tool"
            result = str(getattr(msg, "content", ""))[:200]
            # messages 模式里同一条工具结果可能被多吐一次，去重避免重复事件
            if (name, result) != last_tool_end:
                last_tool_end = (name, result)
                yield sse_event("tool_end", {"name": name, "result": result})
    # 最后一段（通常就是产出答案那次）在流末落账
    if pending_um is not None and usage_rec is not None:
        usage_rec.record_usage_meta(pending_um)
    if usage_rec is not None:
        usage_rec.tool_calls = len(tools_used)


async def _agent_stream(
    question: str,
    allowed_doc_ids: list[str] | None,
    conversation_id: str,
    session_row: ChatSession,
    db: Session,
    trace_id: str,
):
    token = begin_trace(source="agent", conversation_id=conversation_id, trace_id=trace_id)
    try:
        yield sse_event("meta", {"conversation_id": conversation_id, "trace_id": trace_id})
        answer_buf: list[str] = []
        tools_used: list[str] = []
        # async astream 必须配 async checkpointer；同一 sqlite 文件，会话记忆照常跨请求。
        async with open_async_checkpointer() as saver:
            delivered = False  # 是否已吐过内容/tool 事件（meta 除外）
            try:
                async for ev in _run_agent_turn(
                    saver, allowed_doc_ids, conversation_id, question, answer_buf, tools_used
                ):
                    delivered = True
                    yield ev
            except Exception:
                # 主供应商在吐任何内容前就失败（连接级错误）且配了备份：续跑一轮。
                # 已吐过事件后不能重试（客户端已看到前半段）；此刻失败必在首个模型节点、
                # human 已入库 → replay=True 空输入续跑，避免重复提问（同非流式口径）。
                backup = get_agent_backup_llm()
                if delivered or backup is None:
                    raise
                logger.warning(
                    "主供应商生成失败，回退备份供应商重试（agent stream，question=%r）", question[:50]
                )
                async for ev in _run_agent_turn(
                    saver,
                    allowed_doc_ids,
                    conversation_id,
                    question,
                    answer_buf,
                    tools_used,
                    llm=backup,
                    replay=True,
                ):
                    yield ev
        answer = "".join(answer_buf)
        log_answer(question, answer)
        session_row.last_active_at = _now_utc()
        db.commit()
        yield sse_event("done", {"answer": answer, "tools": tools_used})
    except Exception as e:
        yield sse_event("error", {"message": f"模型调用失败：{e}"})
    finally:
        end_trace(token)
