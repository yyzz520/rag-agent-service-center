FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
# 默认官方 PyPI（可移植）；国内网络拉不动时构建传 --build-arg PIP_INDEX_URL=<国内镜像>。
# cache mount：pip 下载缓存落在构建器缓存而非镜像层，重复构建不必重新下载、镜像不膨胀。
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN --mount=type=cache,id=pip,target=/root/.cache/pip \
    pip install --timeout 60 --retries 8 -r requirements.txt --index-url "$PIP_INDEX_URL"

COPY app ./app
# 示例 MCP server：配置 MCP_SERVERS_JSON 时由 app 以子进程方式拉起（stdio 传输）
COPY mcp_servers ./mcp_servers
COPY healthcheck.py .

# 与 config.port 默认值 8200 对齐；docker-compose 也映射 ${PORT:-8200}
EXPOSE 8200

# 依赖容器内 /health（GET 200）判活；PORT 运行时环境变量可改，healthcheck 跟随读取
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "/app/healthcheck.py"]

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8200}"]
