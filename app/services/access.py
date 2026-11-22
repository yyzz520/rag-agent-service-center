"""多租户访问助手：默认知识库、归属校验、本人可检索文档 id 集合。

所有业务接口只通过这里拿"当前用户能碰的东西"，杜绝跨用户越权（验收硬指标：
A 用户查不到 B 用户的文档）。向量隔离的收窄口径 = owned_doc_ids() 返回的 uuid 列表。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import UserContext, load_users
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
    """严格本人范围内的文档 uuid 列表（编辑/删除、知识库管理等写场景用）。"""
    q = select(Doc.id).where(Doc.owner_id == owner_id, Doc.status == status)
    if kb_id:
        q = q.where(Doc.kb_id == kb_id)
    return [r[0] for r in db.execute(q).all()]


# ---- 纵向隔离（配置驱动，默认关闭）----
# 规则：能把"归属人所在组织 = 本组织或其下级"的文档纳入查看范围，且归属人职级不高于本人。
# 没配 org_id 的用户行为与原来一致（只看自己）；admin 不受限。
# 这是"查看权限"，编辑/删除仍走 owned_doc()，不随组织视野放宽。

def _org_in_scope(owner_org: str, viewer_org: str) -> bool:
    return owner_org == viewer_org or owner_org.startswith(viewer_org + "/")


def _user_by_name(username: str) -> dict | None:
    return next((u for u in load_users() if u["username"] == username), None)


def _can_view_owner(owner_id: str, viewer: UserContext) -> bool:
    if viewer.is_admin or owner_id == viewer.user_id:
        return True
    if not viewer.org_id:
        return False  # 未配置组织 = 只看自己，保持旧行为
    owner = _user_by_name(owner_id)
    if owner is None:
        return False
    # 横向优先：组织路径相同也不跨租户（否则两个公司都用 hq/tech 就会串）
    if owner.get("tenant_id", "default") != viewer.tenant_id:
        return False
    owner_org = str(owner.get("org_id") or "")
    if not owner_org or not _org_in_scope(owner_org, viewer.org_id):
        return False
    try:
        owner_level = int(owner.get("level", 1))
    except (TypeError, ValueError):
        owner_level = 1
    return owner_level <= viewer.level


def visible_owner_ids(viewer: UserContext) -> list[str] | None:
    """当前用户可查看的文档归属人；None = 不限制（admin）。"""
    if viewer.is_admin:
        return None
    owners = {u["username"] for u in load_users() if _can_view_owner(u["username"], viewer)}
    owners.add(viewer.user_id)
    return sorted(owners)


def visible_doc(db: Session, viewer: UserContext, doc_id: str) -> Doc | None:
    """查看权限：本人 + 组织子树内职级不高于本人的文档；编辑/删除不要用这个。"""
    doc = db.execute(select(Doc).where(Doc.id == doc_id)).scalar_one_or_none()
    if doc is None:
        return None
    return doc if _can_view_owner(doc.owner_id, viewer) else None


def visible_doc_ids(
    db: Session,
    viewer: UserContext,
    kb_id: str | None = None,
    status: str = "ready",
) -> list[str]:
    """可检索的文档 uuid 列表（横向按租户/归属人，纵向按组织子树 + 职级）。"""
    owners = visible_owner_ids(viewer)
    q = select(Doc.id).where(Doc.status == status)
    if owners is not None:
        q = q.where(Doc.owner_id.in_(owners))
    if kb_id:
        q = q.where(Doc.kb_id == kb_id)
    return [r[0] for r in db.execute(q).all()]
