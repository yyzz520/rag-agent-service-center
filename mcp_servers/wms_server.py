"""示例 MCP server：模拟制药企业的仓储/质量系统（GMP 语境），stdio 传输。

不是要重做一套 WMS：演示重点是 agent 怎么接外部系统、怎么做人工审批和幂等。
真实系统只需按同样协议暴露"查询/操作"接口，agent 侧不用改。

数据落在 data/wms_mock.json（可通过 WMS_MOCK_PATH 覆盖），这样 stdio 每次新起
会话也能读到上一次 release_batch 的结果；删掉该文件即恢复初始假数据。

启动（agent 侧会自己拉起子进程，一般不用手动跑）：
    python -m mcp_servers.wms_server
自测：
    python mcp_servers/wms_server.py --selftest
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("wms")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_lock = threading.Lock()

# 演示用假数据（虚构企业）。真实场景换成查 ERP/MES/LIMS。
_DEFAULT_STORAGE = {
    "QS-2024-9527": {
        "material_type": "成品",
        "zone": "冷藏库 B-03",
        "storage_condition": "2~8℃",
        "qa_status": "已放行",
        "batch_record_ok": True,
        "deviation_open": False,
        "expiry": "2027-06-30",
        "qty": 1200,
    },
    "QS-2024-8831": {
        "material_type": "成品",
        "zone": "阴凉库 A-11",
        "storage_condition": "≤20℃",
        "qa_status": "待检验",
        "batch_record_ok": True,
        "deviation_open": False,
        "expiry": "2026-12-31",
        "qty": 480,
    },
    "QS-2024-7702": {
        "material_type": "原辅料",
        "zone": "常温库 C-05",
        "storage_condition": "常温",
        "qa_status": "合格",
        "batch_record_ok": True,
        "deviation_open": False,
        "expiry": "2028-03-31",
        "qty": 300,
    },
    "QS-2024-6001": {
        "material_type": "成品",
        "zone": "冷藏库 B-01",
        "storage_condition": "2~8℃",
        "qa_status": "待放行",
        "batch_record_ok": True,
        "deviation_open": False,
        "expiry": "2027-09-30",
        "qty": 200,
    },
    "QS-2024-6100": {
        "material_type": "成品",
        "zone": "冷藏库 B-02",
        "storage_condition": "2~8℃",
        "qa_status": "待放行",
        "batch_record_ok": True,
        "deviation_open": True,
        "expiry": "2027-11-30",
        "qty": 150,
    },
}


def _store_path() -> Path:
    return Path(os.environ.get("WMS_MOCK_PATH", _PROJECT_ROOT / "data" / "wms_mock.json"))


def _load() -> dict:
    state = {key: dict(row) for key, row in _DEFAULT_STORAGE.items()}
    path = _store_path()
    if path.exists():
        try:
            override = json.loads(path.read_text(encoding="utf-8"))
            for key, row in override.items():
                state.setdefault(key, {}).update(row)
        except (json.JSONDecodeError, OSError):
            pass
    return state


def _save(state: dict) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _format(key: str, row: dict) -> str:
    record = "批记录完整" if row.get("batch_record_ok") else "批记录不完整"
    deviation = "存在未关闭偏差" if row.get("deviation_open") else "无未关闭偏差"
    return (
        f"批号 {key}（{row.get('material_type', '未知')}）：库区 {row.get('zone')}，"
        f"储存条件 {row.get('storage_condition')}，QA 状态 {row.get('qa_status')}，"
        f"{record}，{deviation}，有效期 {row.get('expiry')}，结存 {row.get('qty')} 件。"
    )


@mcp.tool()
def get_batch_storage(batch_no: str) -> str:
    """查询批号的存放库区、储存条件、QA 状态、批记录/偏差状态、有效期与结存数量。"""
    key = batch_no.strip().upper()
    with _lock:
        state = _load()
    row = state.get(key)
    if row is None:
        return f"未查到批号 {batch_no}（在库批号：{'、'.join(sorted(state))}）"
    return _format(key, row)


@mcp.tool()
def list_batches() -> str:
    """列出当前在库的全部批号。"""
    with _lock:
        state = _load()
    return "在库批号：" + "、".join(sorted(state))


@mcp.tool()
def release_batch(batch_no: str) -> str:
    """对成品执行 QA 放行。仅当状态为待放行、批记录完整且无未关闭偏差时允许。"""
    key = batch_no.strip().upper()
    with _lock:
        state = _load()
        row = state.get(key)
        if row is None:
            return f"未查到批号 {batch_no}，无法放行。"
        if row.get("qa_status") == "已放行":
            return f"批号 {key} 已是已放行状态，未重复执行。"
        if row.get("qa_status") != "待放行":
            return f"批号 {key} 当前状态为 {row.get('qa_status')}，不允许放行。"
        if not row.get("batch_record_ok"):
            return f"批号 {key} 批记录不完整，不允许放行。"
        if row.get("deviation_open"):
            return f"批号 {key} 存在未关闭偏差，不允许放行。"
        row["qa_status"] = "已放行"
        row["released_at"] = datetime.now(timezone.utc).isoformat()
        state[key] = row
        _save(state)
    return f"批号 {key} 已放行（{row['released_at']}）。"


def _selftest() -> None:
    print(get_batch_storage("QS-2024-9527"))
    print(get_batch_storage("qs-2024-9527"))
    print(get_batch_storage("NOT-EXIST"))
    print(list_batches())
    print(release_batch("QS-2024-6100"))  # 有未关闭偏差，应拒绝
    print(get_batch_storage("QS-2024-6001"))


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        _selftest()
    else:
        mcp.run()
