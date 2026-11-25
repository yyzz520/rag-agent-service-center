#!/usr/bin/env python3
"""Skill 本地演示：直连 RAG 链路（只读）。生产调用请走 POST /api/v1/rag/chat。"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from app.rag.chain import run_rag  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="企业知识库问答 Skill 演示")
    parser.add_argument("question")
    parser.add_argument("--doc", default=None, help="限定单文档 doc_id（可选）")
    args = parser.parse_args()
    allowed = [args.doc] if args.doc else None
    print(json.dumps(run_rag(args.question, allowed_doc_ids=allowed), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
