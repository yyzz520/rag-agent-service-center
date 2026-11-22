"""按当前切块策略重建本地 demo 索引（父块 + 子块）。

用于切块策略升级后，把已入库文档重新解析/切块/向量化；不动 source 文件和评测集。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Doc
from app.rag.vector_store import delete_by_doc_id
from app.services.ingest import parse_and_index


def main() -> int:
    db = SessionLocal()
    try:
        docs = db.execute(select(Doc).order_by(Doc.created_at)).scalars().all()
        if not docs:
            print("没有文档可重建；先 make seed")
            return 1
        ids = [d.id for d in docs]
        names = {d.id: d.file_name for d in docs}
    finally:
        db.close()

    failed = 0
    for doc_id in ids:
        delete_by_doc_id(doc_id)
        db = SessionLocal()
        try:
            doc = db.get(Doc, doc_id)
            if doc is None:
                continue
            doc.status = "parsing"
            doc.chunk_count = 0
            doc.error_msg = None
            db.commit()
        finally:
            db.close()
        parse_and_index(doc_id)
        db = SessionLocal()
        try:
            doc = db.get(Doc, doc_id)
            status = doc.status if doc else "missing"
            chunks = doc.chunk_count if doc else 0
        finally:
            db.close()
        print(f"{status:7} chunks={chunks:3} {names.get(doc_id, doc_id)}")
        if status != "ready":
            failed += 1
    print(f"reindex done: {len(ids) - failed}/{len(ids)} ready")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
