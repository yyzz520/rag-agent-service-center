# 设计与取舍

[← 返回 README](../README.md)

## 为什么生产档只有 Postgres、不是 MySQL

会话记忆用的是 LangGraph 官方 checkpointer，**官方只出 sqlite / postgres 两个后端，没有 MySQL saver**。所以生产档选 Postgres，让会话记忆和应用表统一到一个引擎——少一套中间件、能共用连接配置与备份策略。

## 分布式组件怎么起作用

单机版这几个组件都不需要；一旦多副本，它们各自解决一个"进程内状态在多副本下会失效"的问题：

| 组件 | 单副本时的做法 | 多副本为什么不行 | 换成 |
|---|---|---|---|
| 限流 | 进程内计数 dict | N 个副本各自计数，实际上限被放大 N 倍 | Redis `INCR` 全局计数（`app/limiter.py`） |
| 会话记忆 | sqlite checkpointer | 记忆文件在本副本，换副本就"失忆" | Postgres checkpointer（`app/agent/graph.py`） |
| 向量库 | 嵌入式 Chroma 文件 | 进程内独占文件，副本间共享不了 | 独立 Chroma server |
| 检索缓存 | 进程内 / 不缓存 | 缓存命中率被副本数摊薄 | Redis 共享缓存（`app/rag/cache.py`） |

两处**刻意保留降级**、不假装高可用：

- Redis 挂了 → 限流退回进程内计数并打警告：**宁可弱限流（多副本下被放大），也不要因为限流组件故障直接 500**。
- MCP server 连不上 → 不挂载它的工具并告警，agent 用本地工具照常工作；绝不会塞一个"永远返回空"的假工具让模型以为查过了。

## 已知取舍（没做 / 做不了的）

| 项 | 现状 |
|---|---|
| PostgresSaver 连接 | 每请求开一个连接（`from_conn_string`），高并发应换 `psycopg_pool` 连接池 |
| trace / eval 报告 | 仍是 JSONL 文件，多副本下有轮转问题（见 [部署](deployment.md)） |
| agent 评测稳定性 | 只跑一轮看指标，**"同题多跑 N 次看波动"没做**（成本×N） |
| MCP | 只验证了项目自带的示例 server，未接真实第三方；stdio 每次调用起子进程（约 1.5s，见 [MCP](mcp.md)） |
| 限流降级 | Redis 挂 → 退回进程内计数，多副本下等于限流被放大 N 倍（选择"弱限流"而不是"直接 500"） |
| K8s | 未做，docker compose + nginx 已够本项目规模 |
