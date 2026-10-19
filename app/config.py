from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # SiliconFlow 一个 key 通吃 LLM / Embedding / Rerank
    siliconflow_api_key: str = ""
    base_url: str = "https://api.siliconflow.cn/v1"

    # 模型名（以 SiliconFlow 控制台实际可用为准）
    llm_model: str = "deepseek-ai/DeepSeek-V3"
    embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # 端口（避免和服务器上已有服务冲突，可在 .env 改 PORT）
    port: int = 8200

    # 存储
    persist_dir: str = "data/chroma"
    upload_dir: str = "uploads"

    # ---- 鉴权（M1）----
    # 总开关：默认开。本地演示可设 AUTH_ENABLED=false 关掉鉴权直跑（README 两种跑法）
    auth_enabled: bool = True
    # JWT 签名密钥：生产务必在 .env 换成强随机串（换了旧 token 全失效）。默认值>=32 字符避免 PyJWT 告警
    secret_key: str = "dev-only-change-me-please-override-4b96f1"
    token_expire_minutes: int = 60 * 24
    # 登录账号：默认内置 alice/bob(user) + admin(user+admin 两种角色都靠它演示)。
    # 可整体覆盖：demo_users_json=[{"username":"tom","password":"p","role":"user"},...]
    demo_users_json: str = ""

    # ---- 关系库：元数据/会话（M1）----
    # 默认 SQLite 零依赖一键跑（README 友好）；生产/多实例切 Postgres 只换连接串，代码不变。
    # 例：postgresql+psycopg://rag:rag@127.0.0.1:5433/rag
    db_url: str = "sqlite:///data/app.db"

    # ---- 会话记忆 checkpointer 后端（M4）----
    # sqlite = 本地零依赖档；postgres = 多实例档（LangGraph 官方只出 sqlite/postgres，
    # 没有 MySQL saver）。postgres_dsn 给 psycopg 用（不带 +驱动 后缀）。
    checkpoint_backend: str = "sqlite"
    postgres_dsn: str = "postgresql://rag:rag@127.0.0.1:5433/rag"

    # ---- Redis：跨进程限流 + 检索缓存（M4）----
    # 空 = 不启用：限流退回进程内单机实现、缓存关闭。配了才生效。
    redis_url: str = ""
    rate_limit_window_seconds: int = 60
    cache_enabled: bool = True
    cache_ttl_rewrite: int = 3600      # 查询改写结果（key 只跟问句有关，隔离无关）
    cache_ttl_embedding: int = 86400   # 文本向量（同上，纯函数结果）
    cache_ttl_retrieval: int = 300     # 检索结果（key 带 scope，短 TTL 等新文档入库自然过期）

    # ---- Chroma 部署形态（M4）----
    # 空 = 嵌入式文件持久化（本地档）；填 host 则连独立 Chroma server（多副本档）。
    chroma_host: str = ""
    chroma_port: int = 8000

    # ---- 多供应商槽位（决策10）----
    # M1 只定型结构：上面 siliconflow_api_key/base_url/llm_model 视为 primary，下面留 backup。
    # fallback/熔断切换逻辑 M3 实现（chat/agent/embedding/rerank 全家桶一起改）。
    backup_base_url: str = ""
    backup_api_key: str = ""
    backup_llm_model: str = ""

    # CORS 白名单（逗号分隔）。默认空=不开跨域；前端浏览器要调用时再配
    cors_origins: str = ""

    # 上传限制（M1 基本安全；MIME 嗅探 M3 补）
    max_upload_mb: int = 20

    # ---- MinerU Cloud OCR（扫描/图像 PDF 识别）----
    # loader 探到 PDF 无文字层时走 MinerU v4：上传 → 轮询 → 下载 zip 取 Markdown。
    # 不配 api_key 时扫描件报明确错误（"需配置 MINERU_API_KEY"），文字层 PDF 不受影响。
    mineru_api_key: str = ""
    mineru_base_url: str = "https://mineru.net/api/v4"
    mineru_model_version: str = "vlm"  # pipeline / vlm / MinerU-HTML
    mineru_ocr_timeout: int = 600  # 单份扫描 PDF 最长等待（秒），上限配额内够用

    # agent 循环护栏（6.10）：ReAct 单次最多跑几步，超了 LangGraph 强制停。
    # 默认 LangGraph 就是 25；显式配置 + 观察平均轮数后调，别默默吃默认
    agent_recursion_limit: int = 25

    # ---- 外部 MCP 工具（M5）----
    # 空 = 不接外部工具（默认，保证零配置能跑）。填 JSON 才启用，形如：
    #   {"wms":{"command":"python","args":["-m","mcp_servers.wms_server"],"transport":"stdio"}}
    # command 写 "python" 会替换成当前解释器；cwd 默认项目根（相对路径的 server 也能找到）。
    mcp_servers_json: str = ""
    # 单次外部工具调用 / 首次加载的等待上限（秒）。stdio 档每次调用会拉起一次 server 子进程，
    # 实测约 1.5s；要消掉这个开销就把 server 改成常驻 http 传输（README「外部工具」一节有说明）。
    mcp_tool_timeout: int = 30

    # ---- 长会话压缩（37）----
    # 线程历史超过 compress_min_messages 条时，下一轮开始前把更早消息折成一条摘要
    # SystemMessage 放队首，只保留最近 compress_keep_recent 条原文（含当前待答 question）。
    # 门槛设低方便本地小样本压测（环境变量 COMPRESS_MIN_MESSAGES/COMPRESS_KEEP_RECENT 可覆盖）。
    compress_min_messages: int = 16
    compress_keep_recent: int = 8

    # ---- M3 加固 ----
    # 简单限流：每个客户端每分钟最多多少请求；0 = 关闭（默认关，演示不误伤）
    rate_limit_per_min: int = 0
    # 部署在自有反向代理（nginx 等）后面时置 True：限流按 X-Forwarded-For 首跳分桶。
    # 不置 True 则按 TCP 对端 IP —— 反代后面那永远是反代的 IP，所有客户端挤同一个桶。
    # 不无条件信任该头：客户端能自己伪造，等于给"换 IP 绕过限流"开后门。
    trust_proxy: bool = False
    # 追加型 JSONL（trace / eval 报告）轮转：文件超过 rotate_bytes 才按行数截尾，
    # 保留最近 keep_entries 条，防磁盘无界增长（设大些避免每次追加都读全文件）
    jsonl_rotate_bytes: int = 5_000_000
    jsonl_keep_entries: int = 2000
    # 模型供应商调用：超时（秒）与重试次数（ChatOpenAI 指数退避由 SDK 处理）
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 3


settings = Settings()
