"""单变量 A/B 实验：make eval-experiment NAME=rerank。"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.experiments import EXPERIMENTS, run_experiment  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, choices=sorted(EXPERIMENTS))
    parser.add_argument("--note", default=None)
    args = parser.parse_args()
    report = run_experiment(args.name, note=args.note)
    print(json.dumps(report, ensure_ascii=False, indent=2))
