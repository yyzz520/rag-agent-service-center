# 接口一览

[← 返回 README](../README.md)

统一响应体 `{"code":0,"message":"success","data":...}`；业务错误用非 0 `code` + HTTP 状态码。

业务接口一律要求 `Authorization: Bearer <token>`（鉴权开关与演示账号见 [部署](deployment.md)）。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/v1/auth/login | 登录换 token（body: username/password） |
| POST | /api/v1/kb | 建知识库（同名不重复）；不建则上传自动进「默认知识库」 |
| GET | /api/v1/kb | 我的知识库列表（含文档数） |
| DELETE | /api/v1/kb/{kb_id} | 删知识库：连文档记录 + 向量 + 源文件一起清 |
| POST | /api/v1/docs | 上传文档（multipart `file`，可带 `kb_id` 指定库）：.txt/.md/.pdf/.docx/.xlsx，默认上限 20MB，文件名消毒 + 内容嗅探防路径穿越/二进制冒充 |
| GET | /api/v1/docs | 我的文档列表（可按 `kb_id`/`status` 过滤），doc_id 作 chat 的 `doc` 参数 |
| GET | /api/v1/docs/{doc_id} | 文档详情：上传后轮询这个到 `ready`/`failed`（失败看 `error_msg`） |
| DELETE | /api/v1/docs/{doc_id} | 删文档：向量块 + 源文件 + 记录一起清 |
| POST | /api/v1/rag/chat | RAG 问答（带 `[n]` 溯源，返回 sources）。body: `question`、`doc`（doc_id，限定单文档；留空 = 本人全库） |
| POST | /api/v1/rag/chat/stream | RAG 流式（SSE）：`meta` → `content`(逐字) → `done`(答案+sources)；异常 → `error` |
| POST | /api/v1/agent/chat | 智能体对话（可调工具：知识库检索/计算器/外部 MCP 工具）。body: `question`、`conversation_id`(续传记忆，不带=新会话)、`doc`(限定检索文档) |
| POST | /api/v1/agent/chat/stream | 智能体流式（SSE）：`meta` → 可交错 `tool_start`/`content`/`tool_end` → `done`(答案+tools) |
| GET | /api/v1/sessions | 我的会话列表（conversation_id 给 agent 续传） |
| DELETE | /api/v1/sessions/{session_id} | 删会话：记录 + 会话记忆一起清 |
| POST | /api/v1/rag/eval | 跑 RAG 检索评测（**只重放检索、不出答案**，所以无 LLM 花费）。body: 可传检索参数覆盖 |
| GET | /api/v1/rag/agent-eval | agent 行为评测历史报告（逐轮对比路由正确率/完成率/平均步数/平均 token） |
| POST | /api/v1/rag/agent-eval | 跑一轮 agent 行为评测（**真跑 LLM、有花费**，任务集见 `data/eval/agent_tasks.json`） |
| GET | /health | 健康检查：chunks / auth_enabled / api_key_ready |

## SSE 事件格式

事件之间空行分隔，形如：

```text
event: content
data: {"delta": "试用"}

event: done
data: {"answer":"试用期为六个月 [1]。","sources":[...]}
```

Agent 流额外有 `tool_start`（工具名）与 `tool_end`（工具名 + 结果摘要），便于前端渲染"正在调用 xxx 工具"。
