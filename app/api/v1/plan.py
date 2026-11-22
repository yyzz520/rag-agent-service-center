"""Plan-and-Execute 接口：生成计划 → 人工审批 → 执行步骤 → 合成答案。

计划用独立的 thread（`conversation_id:plan`），避免和 /agent/chat 的 ReAct 状态互相干扰。
审批一次覆盖整份计划（含计划内的外部工具）；超时按拒绝处理（fail-closed）。
"""
import logging
from typing import Literal

from fastapi import APIRouter, Depends
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.approval import is_expired
from app.agent.graph import open_sync_checkpointer
from app.agent.planner import build_plan_agent
from app.api.responses import error, ok
from app.api.v1.agent import (
    _count_usage_from_messages,
    _interrupt_payload,
    _now_utc,
    _record_tokens,
    _resolve_conversation,
    _resolve_scope,
    _token_budget_error,
)
from app.config import settings
from app.core.quota import enforce_quota
from app.core.security import UserContext
from app.db import get_db
from app.models import Session as ChatSession
from app.rag.trace import begin_trace, end_trace, log_answer, span
from app.services.audit import record as audit_record

logger = logging.getLogger("app.api.plan")

router = APIRouter(prefix="/agent", tags=["智能体 Plan"])


class PlanRequest(BaseModel):
    question: str
    conversation_id: str | None = None
    doc: str | None = None


class PlanApprovalRequest(BaseModel):
    conversation_id: str
    decision: Literal["approve", "reject", "edit"]
    steps: list[dict] | None = None  # decision=edit 时替换整份计划
    doc: str | None = None


def _plan_config(conversation_id: str) -> dict:
    return {
        "configurable": {"thread_id": f"{conversation_id}:plan"},
        "recursion_limit": max(25, settings.agent_plan_max_steps * 3 + 10),
    }


def _plan_summary(payload: dict | None) -> dict:
    payload = payload or {}
    return {
        "plan": payload.get("steps", []),
        "expires_in": payload.get("expires_in"),
        "created_at": payload.get("created_at"),
    }


def _answer_and_results(result: dict, prior_len: int) -> tuple[str, list[dict], list[str]]:
    messages = result.get("messages") or []
    new_msgs = messages[prior_len:]
    answer = messages[-1].content if messages else ""
    results = result.get("results") or []
    tools = [r.get("tool") for r in results if r.get("tool")]
    _count_usage_from_messages(new_msgs, tool_count=len(tools))
    return answer, results, tools


@router.post("/plan", summary="Plan-and-Execute：先出计划等人审批，再执行")
def agent_plan(
    req: PlanRequest,
    user: UserContext = Depends(enforce_quota),
    db: Session = Depends(get_db),
):
    question = req.question.strip()
    if not question:
        return error(40001, "question 不能为空")
    allowed, err = _resolve_scope(db, user, req.doc)
    if err is not None:
        return err
    conversation_id, session_row, err = _resolve_conversation(
        db, user, req.conversation_id, question
    )
    if err is not None:
        return err
    berr = _token_budget_error(user)
    if berr is not None:
        return berr

    token = begin_trace(source="agent", conversation_id=conversation_id)
    try:
        with open_sync_checkpointer() as saver:
            agent = build_plan_agent(
                allowed, checkpointer=saver, conversation_id=conversation_id,
                scope_doc=req.doc,
            )
            config = _plan_config(conversation_id)
            snap = agent.get_state(config)
            pending = _interrupt_payload(snap)
            if pending is not None:
                if is_expired(pending):
                    # 旧计划超时：先按拒绝恢复，再接受新问题
                    prior2 = len(list((snap.values or {}).get("messages") or []))
                    result = agent.invoke(Command(resume={"type": "reject"}), config=config)
                    _count_usage_from_messages(result["messages"][prior2:], tool_count=0)
                    for p in pending.get("steps", []):
                        audit_record(db, user, "agent.plan_approve", conversation_id,
                                     f"{p.get('tool')}:reject（计划超时自动拒绝）")
                else:
                    session_row.last_active_at = _now_utc()
                    db.commit()
                    return ok({
                        "conversation_id": conversation_id,
                        "status": "awaiting_plan_approval",
                        **_plan_summary(pending),
                    })

            prior_len = len(list((agent.get_state(config).values or {}).get("messages") or []))
            with span("plan.run", steps=settings.agent_plan_max_steps):
                result = agent.invoke(
                    {"messages": [HumanMessage(content=question)]}, config=config
                )
            payload = _interrupt_payload(result)
            if payload is not None:
                _count_usage_from_messages(result["messages"][prior_len:], tool_count=0)
                session_row.last_active_at = _now_utc()
                db.commit()
                return ok({
                    "conversation_id": conversation_id,
                    "status": "awaiting_plan_approval",
                    **_plan_summary(payload),
                })

            answer, results, tools = _answer_and_results(result, prior_len)
            log_answer(question, answer)
            session_row.last_active_at = _now_utc()
            db.commit()
            return ok({
                "conversation_id": conversation_id,
                "status": "done",
                "answer": answer,
                "steps": results,
                "tools": tools,
            })
    except Exception as e:
        logger.warning("计划生成失败: %s", e, exc_info=True)
        return error(50001, f"计划生成失败：{e}", status=500)
    finally:
        _record_tokens(user.tenant_id)
        end_trace(token)


@router.post("/plan/approve", summary="审批计划并执行：approve / reject / edit")
def agent_plan_approve(
    req: PlanApprovalRequest,
    user: UserContext = Depends(enforce_quota),
    db: Session = Depends(get_db),
):
    conversation_id = req.conversation_id.strip()
    if not conversation_id:
        return error(40001, "conversation_id 不能为空")
    row = db.execute(
        select(ChatSession).where(
            ChatSession.owner_id == user.user_id, ChatSession.id == conversation_id
        )
    ).scalar_one_or_none()
    if row is None:
        return error(40002, "会话不存在或无权访问")
    allowed, err = _resolve_scope(db, user, req.doc)
    if err is not None:
        return err

    token = begin_trace(source="agent", conversation_id=conversation_id)
    try:
        with open_sync_checkpointer() as saver:
            agent = build_plan_agent(
                allowed, checkpointer=saver, conversation_id=conversation_id,
                scope_doc=req.doc,
            )
            config = _plan_config(conversation_id)
            snap = agent.get_state(config)
            pending = _interrupt_payload(snap)
            if pending is None:
                return error(40003, "当前没有待审批的计划")
            if pending.get("doc") != req.doc:
                return error(40003, "检索范围与原计划不一致，请带上创建计划时的 doc 参数")

            decision: dict = {"type": req.decision}
            expired = is_expired(pending)
            if expired:
                decision = {"type": "reject"}
            elif req.decision == "edit":
                decision["steps"] = req.steps or []
            audit_record(
                db, user, "agent.plan_approve", conversation_id,
                f"{decision['type']}{'（计划超时自动拒绝）' if expired else ''}",
            )

            prior_len = len(list((snap.values or {}).get("messages") or []))
            with span("plan.approve", decision=decision["type"]):
                result = agent.invoke(Command(resume=decision), config=config)
            payload = _interrupt_payload(result)
            row.last_active_at = _now_utc()
            db.commit()
            if payload is not None:
                return ok({
                    "conversation_id": conversation_id,
                    "status": "awaiting_plan_approval",
                    **_plan_summary(payload),
                })

            answer, results, tools = _answer_and_results(result, prior_len)
            log_answer("", answer)
            return ok({
                "conversation_id": conversation_id,
                "status": "done",
                "decision": decision["type"],
                "expired": expired,
                "answer": answer,
                "steps": results,
                "tools": tools,
            })
    except Exception as e:
        logger.warning("计划审批失败: %s", e, exc_info=True)
        return error(50001, f"计划审批失败：{e}", status=500)
    finally:
        _record_tokens(user.tenant_id)
        end_trace(token)
