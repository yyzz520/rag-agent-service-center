"""审计日志写入：失败不影响业务，只记 warning。"""
import logging

from sqlalchemy.orm import Session

from app.core.security import UserContext
from app.models import AuditLog

logger = logging.getLogger("app.audit")


def record(
    db: Session,
    user: UserContext,
    action: str,
    resource: str = "",
    detail: str = "",
) -> None:
    try:
        db.add(AuditLog(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            action=action,
            resource=resource,
            detail=detail[:2000],
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("审计写入失败 action=%s user=%s: %s", action, user.user_id, exc)
