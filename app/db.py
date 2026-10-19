from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


def _connect_args(db_url: str) -> dict:
    if db_url.startswith("sqlite"):
        # SQLite 默认连接只能被创建它的线程用；FastAPI 请求在线程池、后台解析另起线程，
        # 不关这个开关会报 "created in a thread can only be used in that same thread"。
        return {"check_same_thread": False}
    return {}


def _ensure_parent_dir(db_url: str) -> None:
    if db_url.startswith("sqlite:///") and ":memory:" not in db_url:
        parent = Path(db_url.removeprefix("sqlite:///")).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)


_ensure_parent_dir(settings.db_url)

engine = create_engine(
    settings.db_url,
    connect_args=_connect_args(settings.db_url),
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：每个请求一个会话，请求结束必关。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """幂等建表：表已存在则跳过，可反复调用。"""
    from app import models  # noqa: F401  # 必须先把 ORM 表注册进 Base.metadata

    Base.metadata.create_all(engine)
