import logging
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.responses import error, ok
from app.api.sse import sse_event
from app.core.security import UserContext, get_current_user, require_admin
from app.db import get_db
from app.rag.chain import get_backup_llm, make_answer_chain, retrieve_context, run_rag

logger = logging.getLogger("app.api.rag")
from app.rag.agent_eval import read_agent_reports, run_agent_eval
from app.rag.eval import read_reports, run_eval
from app.rag.trace import begin_trace, current_usage, end_trace, log_answer, read_trace
from app.rag.vector_store import get_vectorstore
from app.services.access import owned_doc, owned_doc_ids

router = APIRouter(prefix="/rag", tags=["知识库 RAG"])


class ChatRequest(BaseModel):
    question: str
    doc: str | None = None  # 文档 ID（/docs 返回的 doc_id）；留空=本人知识库全库


class EvalRequest(BaseModel):
    note: str | None = Field(default=None, description="给这次运行打个标签，便于和历史里的其他运行区分")
    # 检索参数覆盖：不填 = 用 retrieval.RAGRetriever 的默认值；黄金问题集固定 →
    # 分数变化只可能来自参数 → GET /eval 历史里两次运行对比就是参数对比。
    candidate_pool: int | None = Field(default=None, ge=1)
    per_query_top: int | None = Field(default=None, ge=1)
    max_docs: int | None = Field(default=None, ge=1)
    use_rerank: bool | None = Field(default=None)


class AgentEvalRequest(BaseModel):
    note: str | None = Field(default=None, description="给这次运行打个标签，便于和历史里的其他运行区分")


def _resolve_scope_or_error(
    db: Session, user: UserContext, doc_id: str | None
) -> tuple[list[str] | None, object | None]:
    """返回 (allowed_doc_ids, None) 或 (None, error 响应)。None 只在"doc 归属失败"时出现。"""
    if doc_id:
        doc = owned_doc(db, user.user_id, doc_id)
        if doc is None:
            return None, error(40002, "文档不存在或无权访问")
        return [doc.id], None
    return owned_doc_ids(db, user.user_id), None


@router.post("/chat", summary="RAG 知识库问答（带溯源，仅限本人文档）")
def chat(
    req: ChatRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    question = req.question.strip()
    if not question:
        return error(40001, "question 不能为空")
    allowed, err = _resolve_scope_or_error(db, user, req.doc)
    if err is not None:
        return err
    token = begin_trace(source="rag")
    try:
        try:
            out = run_rag(question, allowed_doc_ids=allowed)
        except Exception:
            if get_backup_llm() is None:
                raise  # 没配备份供应商：直接按失败处理
            logger.warning("主供应商生成失败，回退备份供应商重试（question=%r）", question[:50])
            out = run_rag(question, allowed_doc_ids=allowed, llm=get_backup_llm())
        log_answer(question, out["answer"])
        return ok({
            "question": question,
            "doc": req.doc,
            "answer": out["answer"],
            "sources": out["sources"],
        })
    except Exception as e:
        return error(50001, f"模型调用失败：{e}", status=500)
    finally:
        end_trace(token)


@router.post("/chat/stream", summary="RAG 流式问答（SSE）：meta → content(逐字) → done(answer+sources) / error")
def chat_stream(
    req: ChatRequest,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    question = req.question.strip()
    if not question:
        return error(40001, "question 不能为空")
    allowed, err = _resolve_scope_or_error(db, user, req.doc)
    if err is not None:
        return err
    trace_id = uuid.uuid4().hex[:12]
    return StreamingResponse(
        _rag_stream(question, allowed, trace_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_answer(question: str, ctx: dict):
    """逐字吐生成文本。主供应商在首个 token 前就失败（连接级错误）且配了备份时，
    用备份 LLM 整体重放；已吐过字后再失败无法重试（会输出重复/截断），直接抛给上层。"""
    yielded_any = False
    rec = current_usage()

    async def _run(chain):
        nonlocal yielded_any
        # 流式下每个 chunk 都带累计 usage_metadata，不能逐个入账（会翻几十倍）。
        # 只留最后一次，流结束记一次 = 这次模型调用的完整消耗。
        last_um = None
        async for delta in chain.astream(
            {"context": ctx["context"], "question": question}
        ):
            text = getattr(delta, "content", None)
            if text is not None and not isinstance(text, str):
                text = str(text)
            if text:
                yielded_any = True
                yield text
            um = getattr(delta, "usage_metadata", None)
            if um:
                last_um = um
        if last_um and rec is not None:
            rec.record_usage_meta(last_um)

    try:
        async for text in _run(make_answer_chain()):
            yield text
    except Exception:
        if yielded_any or get_backup_llm() is None:
            raise
        logger.warning("主供应商生成失败，回退备份供应商重试（stream，question=%r）", question[:50])
        async for text in _run(make_answer_chain(get_backup_llm())):
            yield text


async def _rag_stream(question: str, allowed_doc_ids: list[str], trace_id: str):
    token = begin_trace(source="rag", trace_id=trace_id)
    try:
        yield sse_event("meta", {"trace_id": trace_id})
        ctx = retrieve_context(question, allowed_doc_ids=allowed_doc_ids)
        if ctx is None:
            answer = "资料中没有相关内容，无法回答。"
            log_answer(question, answer)
            yield sse_event("done", {"answer": answer, "sources": []})
            return
        buf: list[str] = []
        async for delta in _stream_answer(question, ctx):
            buf.append(delta)
            yield sse_event("content", {"delta": delta})
        answer = "".join(buf)
        log_answer(question, answer)
        yield sse_event("done", {"answer": answer, "sources": ctx["sources"]})
    except Exception as e:
        # 断连/异常：try/finally 保证 trace 收尾；错误透给前端
        yield sse_event("error", {"message": f"模型调用失败：{e}"})
    finally:
        end_trace(token)


# ---- 内部诊断接口：全局功能，只对管理员开放（6.9#1/#2，鉴权关闭时本地兜底即 admin）----


@router.get("/trace", summary="查看检索/回答 trace（内部诊断，仅管理员）")
def list_trace(_: UserContext = Depends(require_admin)):
    items = read_trace()[-50:]
    return ok({"total": len(items), "items": items})


@router.get("/eval", summary="查看评测历史（内部诊断，仅管理员）")
def list_eval_runs(_: UserContext = Depends(require_admin)):
    runs = read_reports()
    latest = runs[-1] if runs else None
    summary = [
        {"run": i, "note": r.get("note"), "params": r.get("params"), "metrics": r.get("metrics")}
        for i, r in enumerate(runs, start=1)
    ]
    return ok({"total": len(runs), "latest": latest, "summary": summary})


@router.post("/eval", summary="跑一轮评测（仅管理员；同一组题改参重跑即可对比）")
def run_eval_now(
    req: EvalRequest = EvalRequest(),
    _: UserContext = Depends(require_admin),
):
    overrides = {
        "candidate_pool": req.candidate_pool,
        "per_query_top": req.per_query_top,
        "max_docs": req.max_docs,
        "use_rerank": req.use_rerank,
    }
    try:
        report = run_eval(note=req.note, overrides=overrides)
    except ValueError as e:
        return error(40001, str(e))
    except Exception as e:
        return error(50001, f"评测失败：{e}", status=500)
    return ok(report)


@router.get("/agent-eval", summary="查看 agent 行为评测历史（内部诊断，仅管理员）")
def list_agent_eval_runs(_: UserContext = Depends(require_admin)):
    runs = read_agent_reports()
    latest = runs[-1] if runs else None
    summary = [
        {"run": i, "note": r.get("note"), "metrics": r.get("metrics")}
        for i, r in enumerate(runs, start=1)
    ]
    return ok({"total": len(runs), "latest": latest, "summary": summary})


@router.post("/agent-eval", summary="跑一轮 agent 行为评测（仅管理员；真调 LLM，有 token 花费）")
def run_agent_eval_now(
    req: AgentEvalRequest = AgentEvalRequest(),
    _: UserContext = Depends(require_admin),
):
    try:
        report = run_agent_eval(note=req.note)
    except ValueError as e:
        return error(40001, str(e))
    except Exception as e:
        return error(50001, f"agent 评测失败：{e}", status=500)
    return ok(report)


@router.get("/chunks", summary="查看已入库的分块内容（内部调试，仅管理员）")
def list_chunks(_: UserContext = Depends(require_admin)):
    data = get_vectorstore().get(include=["documents", "metadatas"])
    items = [
        {"id": cid, "content": data["documents"][i], "metadata": data["metadatas"][i]}
        for i, cid in enumerate(data["ids"])
    ]
    return ok({"total": len(items), "items": items})
