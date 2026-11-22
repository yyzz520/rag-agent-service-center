"""危险工具执行台账：按 (scope, tool_call_id) 持久化"已提交执行"，重放时不重复执行。

语义是 **at-most-once**：执行前先落账。崩溃后重放时宁可跳过（返回"已提交，请人工复核"），
也不重复产生副作用——对转账/写库/发消息这类操作，重复比漏执行更危险。
幂等键用 tool_call_id：LangGraph 恢复/重试时会复用同一个 id；外部系统若支持幂等键，
建议把同一个 id 一并透传，做到端到端幂等。
"""
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from app.config import settings

_lock = threading.Lock()


def _db(path: str | None = None) -> Path:
    return Path(path or settings.agent_exec_ledger_path)


def _connect(path: str | None = None) -> sqlite3.Connection:
    p = _db(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS executions ("
        "key TEXT PRIMARY KEY, tool TEXT, args TEXT, created_at REAL)"
    )
    return conn


def _key(scope: str, call_id: str) -> str:
    return f"{scope}:{call_id}"


def was_executed(scope: str, call_id: str, path: str | None = None) -> bool:
    with _lock, closing(_connect(path)) as conn:
        row = conn.execute(
            "SELECT 1 FROM executions WHERE key = ?", (_key(scope, call_id),)
        ).fetchone()
        return row is not None


def mark_executed(scope: str, call_id: str, tool: str = "", args: str = "", path: str | None = None) -> bool:
    """执行前落账；返回 False 表示此前已落过账（重复调用，调用方应跳过执行）。"""
    with _lock, closing(_connect(path)) as conn:
        try:
            conn.execute(
                "INSERT INTO executions(key, tool, args, created_at) VALUES (?, ?, ?, ?)",
                (_key(scope, call_id), tool, str(args)[:500], time.time()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def prune(days: int = 7, path: str | None = None) -> None:
    with _lock, closing(_connect(path)) as conn:
        conn.execute(
            "DELETE FROM executions WHERE created_at < ?",
            (time.time() - days * 86400,),
        )
        conn.commit()
