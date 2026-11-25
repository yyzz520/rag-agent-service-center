#!/usr/bin/env python3
"""Skill 只读演示：直连 MCP 工具。线上必须走 /agent/chat，外部工具才会经过人工审批。"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from app.agent import toolkit  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="仓储批号状态 Skill 演示")
    parser.add_argument("batch_no")
    args = parser.parse_args()

    tools = {t.name: t for t in toolkit.tools_for_route(None, "tool")}
    tool = tools.get("get_batch_storage")
    if tool is None:
        print(
            "未配置外部工具（MCP_SERVERS_JSON 为空），无法查询实时状态。",
            file=sys.stderr,
        )
        return 2
    print(tool.invoke({"batch_no": args.batch_no}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
