from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.graph import delete_checkpoint_thread
from app.api.responses import error, ok
from app.core.security import UserContext, get_current_user
from app.db import get_db
from app.models import Session as ChatSession

router = APIRouter(prefix="/sessions", tags=["会话 Session"])


@router.get("", summary="我的会话列表（conversation_id 给 agent/chat 续传记忆用）")
def list_sessions(
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(ChatSession)
        .where(ChatSession.owner_id == user.user_id)
        .order_by(ChatSession.last_active_at.desc())
    ).scalars().all()
    items = [
        {
            "conversation_id": s.id,
            "title": s.title,
            "created_at": s.created_at,
            "last_active_at": s.last_active_at,
        }
        for s in rows
    ]
    return ok({"total": len(items), "items": items})


@router.delete("/{session_id}", summary="删除会话：记录 + checkpointer 里的消息一起清")
def delete_session(
    session_id: str,
    user: UserContext = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.execute(
        select(ChatSession).where(
            ChatSession.owner_id == user.user_id, ChatSession.id == session_id
        )
    ).scalar_one_or_none()
    if row is None:
        return error(40002, "会话不存在或无权访问")
    delete_checkpoint_thread(session_id)
    db.delete(row)
    db.commit()
    return ok({"conversation_id": session_id})
