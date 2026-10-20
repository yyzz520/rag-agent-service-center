# 部署与多副本

[← 返回 README](../README.md)

## 两档对比

代码不变、只改配置：

| 档 | 元数据 / 会话记忆 | 限流与缓存 | 向量库 | 适用 |
|---|---|---|---|---|
| 零依赖（默认） | SQLite 文件 | 进程内 | 嵌入式 Chroma（文件） | 本机一键跑、看功能 |
| 生产形态 | Postgres | Redis | Chroma server | 单机 compose 复现生产拓扑 |

这里的「生产形态」指组件组合与状态外置方式，**不是生产编排方案**：部署工具仍是 docker compose + nginx（K8s 未做），适合单机部署或每台宿主机各跑一套。

关系库为什么只有 Postgres、不是 MySQL：会话记忆用的是 LangGraph 官方 checkpointer，**官方只出 sqlite / postgres 两个后端，没有 MySQL saver**。所以生产形态选 Postgres，让会话记忆和应用表统一到一个引擎——少一套中间件、能共用连接配置与备份策略。

## 前置

注册 [SiliconFlow](https://cloud.siliconflow.cn) 拿一个 API Key（新用户有免费额度），三种模型默认名见 `.env.example`。

上传扫描/图像 PDF 需另配 `MINERU_API_KEY`（[MinerU](https://mineru.net)，可选；不配则扫描件解析失败并提示需配置，文字层 PDF 不受影响）。

## 从零部署（Docker Compose，推荐）

一台机器从零起完整服务（app + nginx + Postgres + Redis + Chroma）。本机、云服务器都适用；服务器上的额外改动见[下一节](#部署到服务器要改什么)。

### 0. 环境要求

- Linux（或 Windows / macOS + Docker Desktop）
- Docker Engine 24+、Compose v2
- 2 核 4G 起步（app 限 2G，中间件合计约 1G）
- 能出网访问 `api.siliconflow.cn`

```bash
docker --version
docker compose version    # 需要 v2：命令是 docker compose，不是 docker-compose
```

没装 Docker 按官方文档装：<https://docs.docker.com/engine/install/>。

### 1. 拉代码

```bash
git clone https://github.com/yyzz520/rag-agent-service-center.git
cd rag-agent-service-center
```

### 2. 配置 `.env`

```bash
cp .env.example .env
```

至少改两项：

- `SILICONFLOW_API_KEY=sk-...`：必填，否则 RAG/Agent 调不通；
- `SECRET_KEY=`：换成随机串，`openssl rand -hex 32` 生成一个粘进去（换掉后旧 token 全部失效）。

可选：`MINERU_API_KEY`（扫描件 OCR）。其余保持默认即可——compose 会用服务名覆盖 `DB_URL` / `POSTGRES_DSN` / `REDIS_URL` / `CHROMA_HOST`，**不要**在容器部署时把它们改成 `localhost`。

### 3. 构建并启动

```bash
docker compose up -d --build
```

首次会拉基础镜像并安装 Python 依赖，约 5–10 分钟。国内默认走腾讯 PyPI 镜像（compose 的 build arg），网络不通时可用 `PIP_INDEX_URL=<你的镜像> docker compose build` 覆盖。

```bash
docker compose ps     # app / postgres / redis / chroma 应显示 healthy
```

### 4. 验证

```bash
curl -s http://localhost:8201/health
```

```json
{"code":0,"message":"success","data":{"status":"ok","chunks":0,"api_key_ready":true,"auth_enabled":true}}
```

- `api_key_ready: false` → key 没填或填错；改完 `.env` 后 `docker compose up -d --force-recreate app`；
- `chunks: 0` → 向量库还是空的（还没传文档），正常；
- 浏览器打开 `http://<服务器IP>:8201/docs` 能看到 Swagger。

### 5. 跑通第一个请求

```bash
# 1) 登录拿 token（没装 python3 就在 Swagger 里手动登录复制）
TOKEN=$(curl -s -X POST http://localhost:8201/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"alice123"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["token"])')

# 2) 上传一份文档（README.md 本身就能拿来烟测）
curl -s -X POST http://localhost:8201/api/v1/docs \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@./README.md"
# → 返回 doc_id，状态 parsing

# 3) 轮询到 ready（把 <doc_id> 换成上一步返回值）
curl -s http://localhost:8201/api/v1/docs/<doc_id> \
  -H "Authorization: Bearer $TOKEN"

# 4) 提问，答案带 [1] 引用和 sources
curl -s -X POST http://localhost:8201/api/v1/rag/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"question":"这个项目有哪些主要能力？"}'
```

### 6. 扩容到多副本（可选）

```bash
docker compose up -d --scale app=3
docker compose ps     # 会出现 3 个 app-* 容器
```

入口仍是 `http://localhost:8201`，nginx 轮转。会话记忆在 Postgres、限流在 Redis、向量在 Chroma、JWT 用同一个 `SECRET_KEY` 验签，所以请求落到哪个副本都正确。

### 7. 常用运维

```bash
docker compose logs -f app                 # 跟日志，Ctrl+C 退出
docker compose restart app                 # 只重启 app
git pull && docker compose up -d --build   # 更新代码
docker compose down                        # 停止并删容器；data/、uploads/ 是宿主机目录，会保留
```

### 常见启动失败

| 现象 | 原因 / 处理 |
|---|---|
| `env file .../.env not found` | 忘了 `cp .env.example .env` |
| `/health` 里 `api_key_ready: false` | key 没填或填错；改完 `.env` 后 `docker compose up -d --force-recreate app` |
| app 一直 `unhealthy` | `docker compose logs app`；多数是依赖未就绪，或模型名 / 网络问题 |
| `port is already allocated` | `8201` / `5433` / `6380` / `8001` 被占，改 `docker-compose.yml` 里冒号左侧的宿主端口 |
| 构建卡在 pip | 网络问题，用 `PIP_INDEX_URL=<镜像> docker compose build` 覆盖后重试 |
| 上传后文档 `failed` | `GET /docs/{id}` 看 `error_msg`；扫描/图像 PDF 需配 `MINERU_API_KEY` |

## 本机零依赖档

不启任何中间件：元数据走 SQLite、向量走嵌入式 Chroma 文件、限流在进程内 —— 看功能足够了。需要 Python 3.11+。

```bash
cp .env.example .env      # 填入 SILICONFLOW_API_KEY
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload   # http://localhost:8200/docs
```

验证：`curl -s http://localhost:8200/health`，`api_key_ready` 应为 `true`。

## 变体：app 跑宿主机、只把中间件放容器

改代码时热重载更方便。先起基础设施：

```bash
docker compose up -d postgres redis chroma   # 只起中间件，app 在本机用 uvicorn 跑
```

再把 `.env` 里这几行取消注释，填宿主机映射端口（不是容器内端口）：

```dotenv
DB_URL=postgresql+psycopg://rag:rag@127.0.0.1:5433/rag
CHECKPOINT_BACKEND=postgres
POSTGRES_DSN=postgresql://rag:rag@127.0.0.1:5433/rag
REDIS_URL=redis://127.0.0.1:6380/0
CHROMA_HOST=127.0.0.1
CHROMA_PORT=8001
```

```bash
.venv/bin/uvicorn app.main:app --reload   # http://localhost:8200/docs
```

注意：嵌入式 Chroma（`data/chroma`）和独立 server（`data/chroma-server`）是两份数据，切换形态不会自动同步（见文末）。

## 部署到服务器要改什么

上面的 `localhost:8201` 只是本机复现时的访问地址。真放到 Linux 服务器上，至少改这几处：

- **连接地址**：app 和中间件都在 compose 里时用服务名（默认已配好，如 `postgres:5432`）；app 跑宿主机、中间件在别处时，才把 `.env` 的 `DB_URL` / `POSTGRES_DSN` / `REDIS_URL` / `CHROMA_HOST` 填成实际 IP / 域名。
- **入口与端口**：compose 里 `8201:80` 改成 `80:80` / `443:443`（或前置一层网关），访问地址换成服务器 IP / 域名。
- **TLS 与密钥**：证书挂在 nginx 或前置网关；保持 `AUTH_ENABLED=true`，JWT 密钥与演示账号走环境变量，不进仓库。
- **网络**：只暴露 nginx。Postgres / Redis / Chroma 现在的 `5433` / `6380` / `8001` 宿主映射是给本机开发用的，上服务器要去掉或绑到 `127.0.0.1`。
- **数据**：`data/`、`uploads/` 挂持久卷并纳入备份；多副本下 trace 的 JSONL 轮转问题仍在（见文末）。

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
# 登录（Docker 部署走 nginx 的 8201；本机 uvicorn 直接跑则换成 8200）
curl -X POST http://localhost:8201/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"alice123"}'
# → data.token

# 上传文档（立刻返回 doc_id + parsing，后台解析完轮询 /docs/{id} 到 ready/failed）
curl -X POST http://localhost:8201/api/v1/docs \
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
