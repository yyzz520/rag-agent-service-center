"""检索上下文注入防护：把资料里的“忽略以上指令”类文本过滤掉。

不是完整的内容安全方案，只做 RAG 场景的第一道：检索回来的内容是资料，不是命令。
"""
import re

_SUSPICIOUS = re.compile(
    r"(ignore\s+(all\s+)?previous|disregard\s+(all\s+)?previous|"
    r"忽略\s*(以上|上述|之前)|system\s*prompt|you\s+are\s+now|"
    r"developer\s+message|act\s+as\s+system)",
    re.IGNORECASE,
)
_FILTERED = "[已过滤可疑指令]"


def sanitize_context(text: str) -> str:
    lines = []
    for line in text.splitlines():
        lines.append(_FILTERED if _SUSPICIOUS.search(line) else line)
    return "\n".join(lines)
