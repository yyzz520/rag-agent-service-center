"""只读巡检：列出 uploads/ 下可安全清理的空目录，跟库里 doc 表对一遍。

背景：删文档时 delete_doc_files 只 unlink 文件、不 rmdir 父目录，于是
uploads/{owner}/{doc_id}/ 会留下一批空壳目录。这个脚本扫出来、逐条判定，
把结论列给人工确认。

只读：不删任何文件、不写数据库。真正的删除等你看完清单再单独做。
用法：.venv/bin/python scripts/audit_uploads.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Doc  # noqa: E402


def _redact(dsn: str) -> str:
    """打日志时把连接串里的密码抹掉。"""
    if "://" not in dsn or "@" not in dsn:
        return dsn
    head, tail = dsn.split("://", 1)
    creds, host = tail.split("@", 1)
    if ":" in creds:
        user = creds.split(":", 1)[0]
        return f"{head}://{user}:***@{host}"
    return dsn


def _is_empty(d: Path) -> bool:
    return not any(d.iterdir())


def main() -> None:
    root = Path(settings.upload_dir)
    print(f"扫目录：{root.resolve()}")
    print(f"查数据库：{_redact(settings.db_url)}")
    if not root.exists():
        raise SystemExit(f"目录不存在：{root}")

    db = SessionLocal()
    try:
        rows = {d.id: d for d in db.query(Doc).all()}
    finally:
        db.close()

    safe, suspicious, normal = [], [], []
    for owner_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for doc_dir in sorted(p for p in owner_dir.iterdir() if p.is_dir()):
            doc_id = doc_dir.name
            empty = _is_empty(doc_dir)
            row = rows.get(doc_id)
            item = (f"{owner_dir.name}/{doc_id}", row)
            if empty and row is None:
                safe.append(item)
            elif empty and row is not None:
                suspicious.append(item)
            elif not empty and row is None:
                suspicious.append(item)
            else:
                normal.append(item)

    print()
    print(f"=== 可安全删除（空目录，且库里无 doc 记录）：{len(safe)} 个 ===")
    for name, _ in safe:
        print(f"  {name}")

    print()
    print(f"=== 需人工判断（空但有记录 / 有文件但无记录）：{len(suspicious)} 个 ===")
    for name, row in suspicious:
        d = root / name
        if _is_empty(d):
            print(f"  {name}  [空目录，但 doc 表有记录]  file_name={row.file_name} status={row.status}")
        else:
            files = ", ".join(p.name for p in d.iterdir())
            print(f"  {name}  [有文件，但 doc 表无记录]  文件={files}")

    print()
    print(f"=== 正常（非空 + 有记录）：{len(normal)} 个（不动）===")
    for name, row in normal:
        print(f"  {name}  status={row.status}  {row.file_name}")

    print()
    print(f"合计 {len(safe) + len(suspicious) + len(normal)} 个目录。本脚本未删任何东西。")


if __name__ == "__main__":
    main()
