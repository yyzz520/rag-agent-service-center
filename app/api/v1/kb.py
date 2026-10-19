from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.responses import error, ok
from app.core.security import UserContext, get_current_user
from app.db import get_db
from app.models import Doc, KB
from app.rag.vector_store import delete_by_doc_id
from app.services.access import owned_doc_ids, owned_kb
from app.services.ingest import delete_doc_files

router = APIRouter(prefix="/kb", tags=["知识库 KB"])


class KbCreate(BaseModel):
    name: str


def _kb_with_counts(db: Session, owner_id: str, kb: KB) -> dict:
    ready = db.execute(
        select(func.count(Doc.id)).where(
            Doc.owner_id == owner_id, Doc.kb_id == kb.id, Doc.status == "ready"
        )
    ).scalar_one()
    total = db.execute(
        select(func.count(Doc.id)).where(Doc.owner_id == owner_id, Doc.kb_id == kb.id)
    ).scalar_one()
    return {
        "id": kb.id,
        "name": kb.name,
        "docs_ready": ready,
        "docs_total": total,
        "created_at": kb.created_at,
    }


@router.post("", summary="创建知识库（同一用户下名称唯一）")
def create_kb(req: KbCreate, user: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    name = (req.name or "").strip()
    if not name:
        return error(40001, "知识库名称不能为空")
    if len(name) > 128:
        return error(40001, "知识库名称过长（最长 128 字）")
    kb = KB(owner_id=user.user_id, name=name)
    db.add(kb)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return error(40002, f"已存在同名知识库「{name}」")
    db.refresh(kb)
    return ok(_kb_with_counts(db, user.user_id, kb))


@router.get("", summary="我的知识库列表（含文档数）")
def list_kb(user: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(KB).where(KB.owner_id == user.user_id).order_by(KB.created_at.desc())
    ).scalars().all()
    items = [_kb_with_counts(db, user.user_id, kb) for kb in rows]
    return ok({"total": len(items), "items": items})


@router.delete("/{kb_id}", summary="删除知识库：库内所有文档的向量块+源文件+记录一起清")
def delete_kb(kb_id: str, user: UserContext = Depends(get_current_user), db: Session = Depends(get_db)):
    kb = owned_kb(db, user.user_id, kb_id)
    if kb is None:
        return error(40002, "知识库不存在或无权访问")
    # 先清向量块和源文件，再删行（cascade 删 doc 记录）
    doc_ids = owned_doc_ids(db, user.user_id, kb_id=kb_id, status="ready")
    for did in doc_ids:
        delete_by_doc_id(did)
    for doc in db.execute(select(Doc).where(Doc.owner_id == user.user_id, Doc.kb_id == kb.id)).scalars():
        delete_doc_files(doc)
    db.delete(kb)
    db.commit()
    return ok({"kb_id": kb_id, "docs_removed": len(doc_ids)})
