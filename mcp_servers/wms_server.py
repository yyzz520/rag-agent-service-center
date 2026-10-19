"""示例 MCP server：模拟一个外部仓储（WMS）系统，用 stdio 传输。

它存在的意义是演示"agent 接外部系统"的能力：知识库只能回答文档里写过的内容，
而"某个批号现在放在哪个库区、是否已放行"这类实时状态不在文档里——必须调外部系统。
这正是 MCP 的定位：把外部能力以统一协议暴露成工具，agent 侧不用为每个系统写适配。

启动（agent 侧会自己拉起子进程，一般不用手动跑）：
    python -m mcp_servers.wms_server
自测：
    python mcp_servers/wms_server.py --selftest
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("wms")

# 演示用假数据：真实场景下这里换成查 WMS 接口/数据库。
_STORAGE = {
    "QS-2024-9527": {"zone": "冷藏库 B-03", "temp": "2~8℃", "status": "已放行", "qty": 1200},
    "QS-2024-8831": {"zone": "阴凉库 A-11", "temp": "≤20℃", "status": "待检验", "qty": 480},
    "QS-2024-7702": {"zone": "常温库 C-05", "temp": "常温", "status": "已放行", "qty": 300},
}


@mcp.tool()
def get_batch_storage(batch_no: str) -> str:
    """查询某个批号在仓库的存放库区、温控要求、放行状态与结存数量。"""
    key = batch_no.strip().upper()
    row = _STORAGE.get(key)
    if row is None:
        return f"未查到批号 {batch_no}（仓库在库批号：{'、'.join(_STORAGE)}）"
    return (
        f"批号 {key}：库区 {row['zone']}，温控要求 {row['temp']}，"
        f"状态 {row['status']}，结存 {row['qty']} 件。"
    )


@mcp.tool()
def list_batches() -> str:
    """列出仓库当前在库的全部批号。"""
    return "在库批号：" + "、".join(_STORAGE)


def _selftest() -> None:
    print(get_batch_storage("QS-2024-9527"))
    print(get_batch_storage("qs-2024-9527"))
    print(get_batch_storage("NOT-EXIST"))
    print(list_batches())


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        _selftest()
    else:
        mcp.run()
