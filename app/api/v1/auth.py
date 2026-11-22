from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.responses import error, ok
from app.core.security import UserContext, authenticate, create_access_token
from app.db import get_db
from app.services.audit import record as audit_record

router = APIRouter(prefix="/auth", tags=["认证 Auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login", summary="登录换 token：拿到后业务接口带 Authorization: Bearer <token>")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    username = (req.username or "").strip()
    if not username or not req.password:
        return error(40001, "用户名和密码不能为空")
    user = authenticate(username, req.password)
    if user is None:
        return error(40101, "用户名或密码错误", status=401)
    token = create_access_token(user)
    user_ctx = UserContext(
        user_id=user["username"],
        username=user["username"],
        role=user.get("role", "user"),
        tenant_id=user.get("tenant_id", "default"),
        org_id=user.get("org_id", ""),
        level=int(user.get("level", 1)),
    )
    audit_record(db, user_ctx, "auth.login", user["username"])
    return ok({
        "token": token,
        "token_type": "bearer",
        "user": {
            "username": user["username"],
            "role": user.get("role", "user"),
            "tenant_id": user_ctx.tenant_id,
            "org_id": user_ctx.org_id,
            "level": user_ctx.level,
        },
    })
