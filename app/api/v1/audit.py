"""审计日志查询（管理员；可按 tenant_id 过滤）。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.responses import ok
from app.core.security import UserContext
from app.core.rbac import require_permission
from app.db import get_db
from app.models import AuditLog

router = APIRouter(prefix="/audit", tags=["审计 Audit"])


@router.get("", summary="审计日志（仅管理员，可按 tenant_id 过滤）")
def list_audit(
    tenant_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: UserContext = Depends(require_permission("audit:read")),
    db: Session = Depends(get_db),
):
    q = select(AuditLog)
    if tenant_id:
        q = q.where(AuditLog.tenant_id == tenant_id)
    rows = db.execute(q.order_by(AuditLog.id.desc()).limit(limit)).scalars().all()
    items = [
        {
            "id": r.id,
            "tenant_id": r.tenant_id,
            "user_id": r.user_id,
            "action": r.action,
            "resource": r.resource,
            "detail": r.detail,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return ok({"total": len(items), "items": items})
