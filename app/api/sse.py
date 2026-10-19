"""SSE 事件格式化。事件 schema：meta / content / tool_start / tool_end / done / error。"""
import json


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
