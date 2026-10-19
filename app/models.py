"""元数据三表：kb / doc / chat_session。向量块本身在 Chroma，这里只放关系型元数据。

设计照改造计划 6.3：
- doc_id 是 chat 的 doc 参数 / 向量块 metadata 里的稳定主键（uuid，替代旧 demo 的路径哈希）
- session.id = conversation_id（与 LangGraph checkpointer 的 thread_id 同值）
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _utc_now() -> datetime:
    # 存 naive UTC：SQLite 无时区概念，统一按 UTC 存，展示层再转本地
    return datetime.now(timezone.utc).replace(tzinfo=None)


class KB(Base):
    __tablename__ = "kb"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_kb_owner_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    docs: Mapped[list["Doc"]] = relationship(back_populates="kb", cascade="all, delete-orphan")


class Doc(Base):
    __tablename__ = "doc"
    __table_args__ = (Index("ix_doc_owner_status", "owner_id", "status"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    kb_id: Mapped[str] = mapped_column(ForeignKey("kb.id"))
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(512))
    # parsing | ready | failed（应用层约束，SQLite 不做真 enum 省迁移麻烦）
    status: Mapped[str] = mapped_column(String(20), default="parsing")
    error_msg: Mapped[str | None] = mapped_column(String(512), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    kb: Mapped["KB"] = relationship(back_populates="docs")


class Session(Base):
    """持久化会话：id 即 conversation_id（= LangGraph thread_id），重启后仍可续传。"""

    __tablename__ = "chat_session"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    last_active_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)
