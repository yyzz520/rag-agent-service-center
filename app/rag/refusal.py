"""拒答收口：整段拒答不应携带引用。"""
import re

REFUSAL_MARKERS = (
    "资料中没有相关内容",
    "未检索到",
    "没有找到",
    "无法回答",
    "没有相关信息",
    "无法提供",
    "资料中未提及",
)


def is_refusal(text: str) -> bool:
    return len(text.strip()) <= 300 and any(m in text for m in REFUSAL_MARKERS)


def strip_citations(text: str) -> str:
    return re.sub(r"\s*\[\d+\]", "", text).strip()


def finalize(text: str, sources: list[dict]) -> tuple[str, list[dict]]:
    """整段拒答时去掉正文引用编号并清空 sources；正常回答原样返回。"""
    if is_refusal(text):
        return strip_citations(text), []
    return text, sources
