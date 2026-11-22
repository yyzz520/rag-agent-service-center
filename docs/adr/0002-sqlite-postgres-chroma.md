# 0002：本地 SQLite/嵌入式 Chroma，多副本切 Postgres/独立 Chroma

## 背景

项目要同时满足两种场景：本地一条命令跑通，以及多副本部署时状态一致。

## 决策

- 元数据/会话：本地 SQLite，多副本切 Postgres；
- LangGraph checkpointer：本地 sqlite，多副本切 Postgres（官方只提供 sqlite/postgres，没有 MySQL）；
- 向量库：本地嵌入式 Chroma 文件，多副本切独立 Chroma server；
- 限流/检索缓存：无 Redis 时旁路或进程内，多副本切 Redis。

## 结果

- 单机零依赖能跑；
- 多副本时四处进程内状态都移到共享组件，换副本不失忆、限流不被放大；
- trace/eval 仍是 JSONL，多副本轮转有已知问题，列入边界。

## 被否方案

- 一开始就上 K8s/Postgres/独立 Chroma：本地演示和 CI 成本高；
- 只用 SQLite：多副本下记忆和限流都错。
