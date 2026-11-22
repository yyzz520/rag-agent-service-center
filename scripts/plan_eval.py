"""Plan-and-Execute 窄场景评测：make eval-plan（真调 LLM，有花费）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.plan_eval import run_plan_eval  # noqa: E402

if __name__ == "__main__":
    report = run_plan_eval(note="cli")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
