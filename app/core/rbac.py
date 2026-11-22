"""最小 RBAC：角色 → 权限；管理接口用 require_permission 收口。

本项目只有 admin/user 两个角色，但接口按权限点命名，后续加角色/权限不用改路由。
"""
from fastapi import Depends, HTTPException

from app.core.security import UserContext, get_current_user

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "user": {"doc:read", "doc:write", "chat:run"},
}


def has_permission(user: UserContext, permission: str) -> bool:
    allowed = ROLE_PERMISSIONS.get(user.role, set())
    return "*" in allowed or permission in allowed


def require_permission(permission: str):
    def dependency(user: UserContext = Depends(get_current_user)) -> UserContext:
        if not has_permission(user, permission):
            raise HTTPException(status_code=403, detail=f"缺少权限: {permission}")
        return user

    return dependency


def require_roles(*roles: str):
    def dependency(user: UserContext = Depends(get_current_user)) -> UserContext:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail=f"需要角色: {','.join(roles)}")
        return user

    return dependency
