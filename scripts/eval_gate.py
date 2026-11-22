"""评测门禁入口：make eval-gate / CI fixture。

仓库根目录执行；只读评测报告，不调模型。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.eval_gate import main  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval", type=Path, default=None)
    parser.add_argument("--generation", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=None)
    args = parser.parse_args()
    raise SystemExit(main(args.retrieval, args.generation, args.baseline))
