"""把业务元数据从 sqlite（data/app.db）搬到当前 DB_URL 指向的库（Postgres）。

换库只改连接串，但旧库里的数据不会自己过去——这个脚本负责搬 kb/doc 两张表。
为什么只搬这两张：检索隔离靠 doc 表算 owned_doc_ids，缺了文档行 Chroma 里的块就成孤儿；
chat_session 不搬——旧会话的消息在 sqlite checkpointer 里，那些文件不会被迁移，
搬过来只会得到一批"记得不上下文"的空会话。

幂等：主键已存在的行跳过，可反复运行。
用法：.venv/bin/python scripts/migrate_sqlite_to_pg.py
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime

# 直接跑脚本时把项目根加进 sys.path，才能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Doc, KB  # noqa: E402

SRC_PATH = Path("data/app.db")
TABLES = [("kb", KB), ("doc", Doc)]


def _coerce(column, value):
    """sqlite 里 DateTime 存的是字符串，pg 的 timestamp 列要 datetime 对象。"""
    if value is not None and isinstance(column.type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def main() -> None:
    if not SRC_PATH.exists():
        raise SystemExit(f"找不到源库：{SRC_PATH}（首次跑就该用 Postgres 空库，无需迁移）")

    src = sqlite3.connect(str(SRC_PATH))
    src.row_factory = sqlite3.Row
    init_db()  # 目标库建表（幂等）

    with SessionLocal() as dst:
        for table, model in TABLES:
            cols = [c.name for c in model.__table__.columns]
            try:
                rows = src.execute(f"select {', '.join(cols)} from {table}").fetchall()
            except sqlite3.OperationalError as e:
                print(f"{table}: 源库没这张表，跳过（{e}）")
                continue
            added = skipped = 0
            for r in rows:
                if dst.get(model, r["id"]) is not None:
                    skipped += 1
                    continue
                dst.add(model(**{c: _coerce(model.__table__.columns[c], r[c]) for c in cols}))
                added += 1
            dst.commit()
            print(f"{table}: 新增 {added}，已存在跳过 {skipped}，源共 {len(rows)}")
    src.close()
    print(f"迁移完成 → {settings.db_url}")


if __name__ == "__main__":
    main()
