"""多租户访问助手：默认知识库、归属校验、本人可检索文档 id 集合。

所有业务接口只通过这里拿"当前用户能碰的东西"，杜绝跨用户越权（验收硬指标：
A 用户查不到 B 用户的文档）。向量隔离的收窄口径 = owned_doc_ids() 返回的 uuid 列表。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Doc, KB

DEFAULT_KB_NAME = "默认知识库"


def get_or_create_default_kb(db: Session, owner_id: str) -> KB:
    """新用户首次上传无知识库时自动建一个默认 kb 归属（6.9#4）。"""
    kb = db.execute(
        select(KB).where(KB.owner_id == owner_id, KB.name == DEFAULT_KB_NAME)
    ).scalar_one_or_none()
    if kb is None:
        kb = KB(owner_id=owner_id, name=DEFAULT_KB_NAME)
        db.add(kb)
        db.commit()
        db.refresh(kb)
    return kb


def owned_doc(db: Session, owner_id: str, doc_id: str) -> Doc | None:
    """取当前用户自己的文档；别人的文档一律 None（=无权/不存在）。"""
    return db.execute(
        select(Doc).where(Doc.owner_id == owner_id, Doc.id == doc_id)
    ).scalar_one_or_none()


def owned_kb(db: Session, owner_id: str, kb_id: str) -> KB | None:
    return db.execute(
        select(KB).where(KB.owner_id == owner_id, KB.id == kb_id)
    ).scalar_one_or_none()


def owned_doc_ids(
    db: Session,
    owner_id: str,
    kb_id: str | None = None,
    status: str = "ready",
) -> list[str]:
    """当前用户范围内可检索的文档 uuid 列表（默认只看 ready）。

    chat/agent 不带 doc 参数 = "我自己知识库里所有可问文档"，最后都落到这个列表，
    检索层再用它做 Chroma/BM25 双路收窄。
    """
    q = select(Doc.id).where(Doc.owner_id == owner_id, Doc.status == status)
    if kb_id:
        q = q.where(Doc.kb_id == kb_id)
    return [r[0] for r in db.execute(q).all()]
