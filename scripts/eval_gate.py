"""评测门禁入口：make eval-gate。

仓库根目录执行；只读最近一次评测报告，不调模型。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.eval_gate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
