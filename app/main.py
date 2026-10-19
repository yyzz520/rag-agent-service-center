import json
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse

from app.agent.graph import init_checkpointer
from app.agent.mcp_tools import get_mcp_tools
from app.api.responses import ok
from app.api.v1 import agent, auth, docs, kb, rag, sessions
from app.config import settings
from app.db import init_db
from app.limiter import limiter
from app.rag.vector_store import count_chunks

# 访问日志：每请求一行 JSON（结构化），trace_id 同时进响应头，便于跨请求关联
access_logger = logging.getLogger("app.access")
if not access_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    access_logger.addHandler(_h)
access_logger.setLevel(logging.INFO)
access_logger.propagate = False

# 兜底异常日志：未被路由层捕获的 500 在这里留完整 traceback，再返回统一错误体
error_logger = logging.getLogger("app.error")
if not error_logger.handlers:
    _eh = logging.StreamHandler()
    _eh.setFormatter(logging.Formatter("%(asctime)s app.error %(levelname)s %(message)s"))
    error_logger.addHandler(_eh)
error_logger.setLevel(logging.ERROR)
error_logger.propagate = False

# 限流计数在 app/limiter.py：配了 REDIS_URL 走 Redis 全局计数（多副本正确），
# 否则退回进程内（单机有效）。这里只负责取 client key 和放行判定。


def _client_key(request: Request) -> str:
    """限流分桶的客户端标识。反代后面 request.client.host 是反代的 IP，
    所有真实客户端会挤进同一个桶——所以配了 TRUST_PROXY 才改取 X-Forwarded-For 首跳。

    为什么不无条件信这个头：客户端能自己伪造，无条件信等于给"换 IP 绕过限流"开后门；
    只有能保证反代会重写它时才开。
    """
    if settings.trust_proxy:
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()  # 幂等建表：kb/doc/chat_session
    init_checkpointer()  # 会话记忆表（postgres 档显式 setup；sqlite 档建文件）
    # 外部 MCP 工具加载要跟 server 握手（秒级），放后台线程预热：否则第一个真实请求
    # 要替整个进程付这笔冷启动延迟。加载失败不影响启动（get_mcp_tools 内部兜底 + 冷却重试）。
    if settings.mcp_servers_json.strip():
        threading.Thread(target=get_mcp_tools, name="mcp-prewarm", daemon=True).start()
    yield


app = FastAPI(
    title="企业 AI 智能中台 · RAG + Agent 服务中心",
    description="以文档为业务对象的 RAG 知识库 + LangGraph 智能体能力层："
    "JWT 鉴权 · 多租户隔离 · 元数据落库 · 会话持久化 · SSE 流式输出 · "
    "多格式解析（txt/md/pdf/docx/xlsx，扫描件自动走 MinerU OCR）",
    version="0.3.0",
    lifespan=lifespan,
)

# CORS：env 配了白名单才开（默认空=不开，服务端/内网直连够用）
if settings.cors_origins.strip():
    from fastapi.middleware.cors import CORSMiddleware

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    # 鉴权/权限失败走统一响应体：{"code","message","data"}，方便前端统一收
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": str(exc.detail), "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    # 参数校验失败(422)也走统一错误体，不再吐 FastAPI 默认 {"detail": [...]}
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    msg = first.get("msg", "请求参数校验失败")
    detail = f"参数校验失败：{loc}：{msg}" if loc else f"参数校验失败：{msg}"
    return JSONResponse(
        status_code=422,
        content={"code": 422, "message": detail, "data": None},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # 兜底：没被路由层接住的异常也回统一错误体；完整 traceback 落 error 日志
    error_logger.error(
        "Unhandled error %s %s", request.method, request.url.path, exc_info=True
    )
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务器内部错误，请查看日志", "data": None},
    )


@app.middleware("http")
async def access_log(request: Request, call_next):
    # 限流（开了才生效）：超限直接 429，仍走统一错误体 + 访问日志
    rate_limited = False
    if settings.rate_limit_per_min > 0 and request.url.path != "/health":
        if await limiter.over_limit(_client_key(request)):
            rate_limited = True
    trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    response = None
    try:
        if rate_limited:
            response = JSONResponse(status_code=429, content={"code": 429, "message": "请求过于频繁，请稍后再试", "data": None})
        else:
            response = await call_next(request)
        return response
    finally:
        # 异常时 response 为 None：记 status 500，异常本体交给 FastAPI 处理
        ms = round((time.perf_counter() - start) * 1000, 1)
        access_logger.info(json.dumps({
            "trace_id": trace_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code if response else 500,
            "ms": ms,
        }, ensure_ascii=False))
        if response is not None:
            response.headers["X-Trace-Id"] = trace_id


app.include_router(auth.router, prefix="/api/v1")
app.include_router(kb.router, prefix="/api/v1")
app.include_router(docs.router, prefix="/api/v1")
app.include_router(rag.router, prefix="/api/v1")
app.include_router(agent.router, prefix="/api/v1")
app.include_router(sessions.router, prefix="/api/v1")


@app.get("/health", summary="健康检查")
def health():
    return ok({
        "status": "ok",
        "chunks": count_chunks(),
        "api_key_ready": bool(settings.siliconflow_api_key),
        "auth_enabled": settings.auth_enabled,
    })


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=True)
