"""数据集 manifest：黄金题集/Agent 任务集的 sha256、条数、字节数，随评测报告落盘。

评测只认数据版本：题集换了或内容改了，sha256 就变，前后两次分数不可直接比较——
报告里带着这个指纹，对比时先看 dataset.sha256 是否一致。
"""
import hashlib
import json
from pathlib import Path


def dataset_manifest(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "missing": True}
    raw = path.read_bytes()
    try:
        items = len(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        items = None
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "items": items,
        "bytes": len(raw),
    }
