"""JWT 自签自验 + 登录账号（env 配）+ 当前用户依赖。

架构口径（改造计划决策3）：公司是网关验签、服务读 user_id；本项目不依赖公司网关，
自己签/验 token，sub=user_id。将来进网关时把"验签"这一层换成网关透传即可，结构等价。
演示账号放 env（内存只读），不建 users 表——账号不是本 demo 要展示的业务。
"""
import json
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import settings

_bearer = HTTPBearer(auto_error=False)

# 内置演示账号：两个普通用户做"多租户隔离"演示，一个 admin 跑全局功能（eval）
_DEFAULT_USERS = [
    {"username": "alice", "password": "alice123", "role": "user"},
    {"username": "bob", "password": "bob123", "role": "user"},
    {"username": "admin", "password": "admin123", "role": "admin"},
]


class UserContext(BaseModel):
    user_id: str
    username: str
    role: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def load_users() -> list[dict]:
    """env 里配了 demo_users_json 就用它，否则用内置默认。"""
    if settings.demo_users_json.strip():
        data = json.loads(settings.demo_users_json)  # 配错 JSON 会让服务启动即报错，值得
        if not isinstance(data, list):
            raise ValueError("demo_users_json 需是账号对象列表")
        return data
    return _DEFAULT_USERS


def authenticate(username: str, password: str) -> dict | None:
    for u in load_users():
        if u["username"] == username and u["password"] == password:
            return u
    return None


def create_access_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["username"],  # env 账号没有独立 id，user_id 就用用户名
        "username": user["username"],
        "role": user.get("role", "user"),
        "iat": now,
        "exp": now + timedelta(minutes=settings.token_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


def _local_context() -> UserContext:
    """AUTH_ENABLED=false 的本地兜底身份：等同老 demo 的"全开"；按管理员给，eval 等管理口也能用。"""
    return UserContext(user_id="demo", username="demo", role="admin")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UserContext:
    if not settings.auth_enabled:
        return _local_context()
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="未登录：请先 POST /auth/login 换 token，之后请求头带 Authorization: Bearer <token>",
        )
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="token 无效或已过期，请重新登录")
    return UserContext(
        user_id=payload["sub"],
        username=payload.get("username", payload["sub"]),
        role=payload.get("role", "user"),
    )


async def require_admin(user: UserContext = Depends(get_current_user)) -> UserContext:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="仅管理员可用（登录 admin 账号）")
    return user
