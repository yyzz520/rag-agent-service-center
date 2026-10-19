# 部署与多副本

[← 返回 README](../README.md)

## 两档对比

代码不变、只改配置：

| 档 | 元数据 / 会话记忆 | 限流与缓存 | 向量库 | 适用 |
|---|---|---|---|---|
| 零依赖（默认） | SQLite 文件 | 进程内 | 嵌入式 Chroma（文件） | 本机一键跑、看功能 |
| 生产栈 | Postgres | Redis | Chroma server | `--scale app=N` 多副本 |

关系库为什么只有 Postgres、不是 MySQL：会话记忆用的是 LangGraph 官方 checkpointer，**官方只出 sqlite / postgres 两个后端，没有 MySQL saver**。所以生产档选 Postgres，让会话记忆和应用表统一到一个引擎——少一套中间件、能共用连接配置与备份策略。

## 前置

注册 [SiliconFlow](https://cloud.siliconflow.cn) 拿一个 API Key（新用户有免费额度），三种模型默认名见 `.env.example`。

上传扫描/图像 PDF 需另配 `MINERU_API_KEY`（[MinerU](https://mineru.net)，可选；不配则扫描件解析失败并提示需配置，文字层 PDF 不受影响）。

## 零依赖档

不启任何中间件：元数据走 SQLite、向量走嵌入式 Chroma 文件、限流在进程内 —— 看功能足够了。

```bash
cp .env.example .env      # 填入 SILICONFLOW_API_KEY
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload   # http://localhost:8200/docs
```

## 生产栈（Postgres + Redis + Chroma server）

只多一条命令启基础设施，再用 `.env` 把 app 指过去（连接串示例见 `.env.example`）：

```bash
docker compose up -d postgres redis chroma   # 只起基础设施，app 在本机用 uvicorn 跑
# .env 里打开：DB_URL / CHECKPOINT_BACKEND=postgres / POSTGRES_DSN / REDIS_URL / CHROMA_HOST
.venv/bin/uvicorn app.main:app --reload
```

Docker 全家桶：

```bash
cp .env.example .env
docker compose up -d --build               # 同样打开 /docs
```

## 多副本（水平扩容）

```bash
docker compose up -d --build --scale app=3   # 入口在 nginx：http://localhost:8201
```

`app` 服务不设 `container_name`、不 publish 端口（多副本抢同一宿主端口必然失败），只在集群内 `expose`，对外统一由 nginx 轮转。

两个 nginx 侧的坑：

- **变量式 `proxy_pass` + `resolver 127.0.0.11 valid=10s`**：否则它只在启动时解析一次服务名并钉死 IP，缩容后会一直往已销毁的副本打。
- **SSE 必须 `proxy_buffering off`**：否则事件被攒在缓冲里，"逐字输出"就没了。

> 扩容的前提是共享组件都切过去了：限流计数、会话记忆、向量库只要有一个还在进程内，多副本就是错的（见 [设计与取舍](design.md)）。

## 两种鉴权跑法

业务接口一律要求 `Authorization: Bearer <token>`。`.env` 里 `AUTH_ENABLED` 控制总开关：

| 模式 | 用法 |
|---|---|
| `AUTH_ENABLED=true`（默认，生产形态） | 先 `POST /auth/login` 换 token，之后每个请求带 token |
| `AUTH_ENABLED=false`（本地免登录演示） | 不校验 token，默认以管理员身份直连，只为快速体验 RAG/Agent |

内置演示账号（可在 `.env` 用 `DEMO_USERS_JSON` 覆盖）：`alice/alice123`(user)、`bob/bob123`(user)、`admin/admin123`(admin)。

`/rag/eval`、`/rag/agent-eval`、`/rag/trace`、`/rag/chunks` 为内部诊断接口，仅管理员可调。

```bash
# 登录
curl -X POST http://localhost:8200/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"alice123"}'
# → data.token

# 上传文档（立刻返回 doc_id + parsing，后台解析完轮询 /docs/{id} 到 ready/failed）
curl -X POST http://localhost:8200/api/v1/docs \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@演示.md"
```

## 数据说明

- `data/`（SQLite + Chroma + trace + 评测报告）、`uploads/`（源文件）默认被 `.gitignore` 忽略，新 clone 需自行上传文档建库。**只有 `data/eval/golden.json` 和 `agent_tasks.json` 两个评测数据集被放行**，保证 clone 后能直接复跑评测。
- 评测黄金问题集在 `data/eval/golden.json`，`POST /rag/eval` 只重放检索、不出答案；同一组题改参重跑后 `GET /rag/eval` 逐行对比分数（recall@k / MRR），归因到参数而非生成波动。
- agent 行为评测任务集在 `data/eval/agent_tasks.json`（路由正确 / 该不该调工具 / 答案是否达标），报告落 `agent_reports.jsonl`。与 RAG 评测最大的不同：**它会真跑 LLM、有花费**，所以只放 5 条固定任务。
- 评测跑的时候会**整体绕过检索缓存**（`cache.bypass()`）：命中缓存会让分数虚高且不可复现，评测必须打真链路。
- `data/trace/trace.jsonl` 每问一条 answer 记录，带 `usage` 字段（`model_calls`/`tool_calls`/`prompt_tokens`/`completion_tokens`/`total_tokens`）——每 run LLM 消耗记账，`GET /rag/trace`（管理员）可查。
- 元数据 schema 变更后旧向量块会不可见（无 user 归属标签），demo 语料可整体重建后再跑评测。

## 两处容易踩的坑

- **`data/chroma`（嵌入式）与 `data/chroma-server`（独立 server）是两份数据，会各自漂移**。切部署形态时不会自动同步，换过去要自己拷一次目录（或重新上传建库）。
- **`data/trace/trace.jsonl` 是追加写文件**：多副本下 N 个副本写同一个挂载卷，行不会交错损坏（单次 write 追加），但**轮转（按行数截尾）在多副本下会互相踩**。生产多副本应把 trace 落到数据库或每副本独立文件，本项目未改。
