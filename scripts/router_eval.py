"""Router 路由评测：make eval-router。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.router_eval import run_router_eval  # noqa: E402

if __name__ == "__main__":
    report = run_router_eval(note="cli")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
