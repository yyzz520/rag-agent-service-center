from app.core.rbac import has_permission
from app.core.security import UserContext


def _user(role: str, tenant: str = "tenant-a") -> UserContext:
    return UserContext(user_id="u", username="u", role=role, tenant_id=tenant)


def test_admin_has_all_permissions():
    assert has_permission(_user("admin"), "eval:run")
    assert has_permission(_user("admin"), "audit:read")
    assert has_permission(_user("admin"), "anything")


def test_user_permissions_are_limited():
    user = _user("user")
    assert has_permission(user, "doc:write")
    assert has_permission(user, "chat:run")
    assert not has_permission(user, "eval:run")
    assert not has_permission(user, "audit:read")
