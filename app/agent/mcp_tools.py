"""MCP 工具接入：把外部 MCP server 暴露的工具加载进 ReAct agent。

为什么接 MCP：知识库只能回答"文档里写过的东西"；而"某批号现在在哪个库区、是否放行"
这类实时状态不在文档里，必须调外部系统。MCP 的价值是**协议统一**——不管对端是 WMS、
ERP 还是别的，agent 侧都按同一套 load → 转 tool → 交给模型 的流程接，不用一家写一份适配。

三个设计点：

1. **配置驱动 + 进程内缓存**：MCP_SERVERS_JSON 里列要接的 server（stdio / http），空 = 不接。
   工具清单加载要跟 server 握手（子进程启动 + initialize），启动后基本不变，所以加载一次缓存住，
   不每请求握手。加载失败按 cooldown 重试，不把一次抖动变成"永久没工具"。

2. **async-only 与同步路由的桥接**：适配器把 MCP 工具实现成协程（StructuredTool 只有
   coroutine、没有 func），同步 invoke 会直接 NotImplementedError。而 /agent/chat 是同步路由，
   FastAPI 把它放线程池里跑——那个线程没有运行中的事件循环，没法自己 await。
   这里给工具补一个同步 func，用一个常驻事件循环线程把协程跑完：只为一个工具就把整条同步
   链路改成 async 不划算（压缩、fallback 重放、usage 记账都得跟着重写一遍）。

3. **不静默降级成"假成功"**：server 连不上就不加载它的工具，并在日志里说清楚；
   绝不会伪造一个空结果的工具塞给模型（那会让模型"看起来调过了、其实什么都没查"）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger("app.agent.mcp")

# 加载失败后的重试冷却：与限流/缓存同一套口径，避免每请求都去付一次失败的握手成本
_RETRY_COOLDOWN = 30.0

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_tools: tuple = ()
_loaded_at = 0.0
_retry_at = 0.0
_lock = threading.Lock()

_loop: asyncio.AbstractEventLoop | None = None


def _bridge_loop() -> asyncio.AbstractEventLoop:
    """常驻事件循环线程：给同步调用方一个能跑协程的地方（线程池里没有运行中的 loop）。"""
    global _loop
    with _lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="mcp-bridge", daemon=True).start()
        return _loop


def _run_coro(coro, timeout: float):
    return asyncio.run_coroutine_threadsafe(coro, _bridge_loop()).result(timeout)


def _flatten(res) -> str:
    """MCP 工具返回的是内容块列表（[{"type":"text","text":...}]），拍平成纯文本给模型。"""
    if isinstance(res, tuple):  # response_format="content_and_artifact" 时是 (content, artifact)
        res = res[0]
    if isinstance(res, str):
        return res
    parts = []
    for block in res or []:
        if isinstance(block, dict):
            parts.append(block.get("text") or json.dumps(block, ensure_ascii=False))
        else:
            parts.append(str(block))
    return "\n".join(p for p in parts if p) or "(工具没有返回内容)"


def _as_text_tool(tool_obj):
    """把 MCP 工具在两条路径上都统一成"返回纯文本"。

    适配器造出来的工具是 response_format="content_and_artifact" + 只有 coroutine，
    直接用有两个问题：同步 invoke 会 NotImplementedError；而且返回的是
    (内容块列表, artifact) 这种结构化二元组，模型看到的是它的 repr，不是可读文本。

    这里保留原工具对象（不重建），只把执行入口换掉——name/description/args_schema 一律不动，
    模型靠 description 决定调不调，重建时抄错一个字都会改变路由行为。
    """
    raw_async = tool_obj.coroutine  # 适配器原始协程：返回 (content_blocks, artifact)

    async def _acall(**kwargs):
        return _flatten(await raw_async(**kwargs))

    def _sync(**kwargs):
        return _flatten(_run_coro(raw_async(**kwargs), settings.mcp_tool_timeout))

    tool_obj.response_format = "content"
    tool_obj.coroutine = _acall
    tool_obj.func = _sync
    return tool_obj


def _servers() -> dict:
    """解析 MCP_SERVERS_JSON。command 写 "python" 时替换成当前解释器路径，
    这样同一条配置在 venv / 容器里都能用，不必写死绝对路径。"""
    raw = (settings.mcp_servers_json or "").strip()
    if not raw:
        return {}
    try:
        servers = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("MCP_SERVERS_JSON 不是合法 JSON，已忽略：%s", e)
        return {}
    for cfg in servers.values():
        if cfg.get("command") == "python":
            cfg["command"] = sys.executable
        cfg.setdefault("cwd", str(_PROJECT_ROOT))
    return servers


async def _load() -> tuple:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    tools = await MultiServerMCPClient(_servers()).get_tools()
    return tuple(_as_text_tool(t) for t in tools)


def get_mcp_tools() -> tuple:
    """进程内缓存的外部工具；未配置、加载失败、或处于重试冷却期时返回空元组。"""
    global _tools, _loaded_at, _retry_at

    if not settings.mcp_servers_json.strip():
        return ()
    if _tools:
        return _tools
    if time.time() < _retry_at:
        return ()

    try:
        loaded = _run_coro(_load(), settings.mcp_tool_timeout)
    except Exception as e:
        _retry_at = time.time() + _RETRY_COOLDOWN
        logger.warning(
            "MCP 工具加载失败（%.0fs 内不再重试，本轮不挂载外部工具）：%s",
            _RETRY_COOLDOWN,
            e,
        )
        return ()

    _tools = loaded
    _loaded_at = time.time()
    logger.info("已加载 %d 个 MCP 工具：%s", len(_tools), [t.name for t in _tools])
    return _tools
