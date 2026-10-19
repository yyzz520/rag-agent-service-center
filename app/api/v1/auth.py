from fastapi import APIRouter
from pydantic import BaseModel

from app.api.responses import error, ok
from app.core.security import authenticate, create_access_token

router = APIRouter(prefix="/auth", tags=["认证 Auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login", summary="登录换 token：拿到后业务接口带 Authorization: Bearer <token>")
def login(req: LoginRequest):
    username = (req.username or "").strip()
    if not username or not req.password:
        return error(40001, "用户名和密码不能为空")
    user = authenticate(username, req.password)
    if user is None:
        return error(40101, "用户名或密码错误", status=401)
    token = create_access_token(user)
    return ok({
        "token": token,
        "token_type": "bearer",
        "user": {"username": user["username"], "role": user.get("role", "user")},
    })
