# Contributing

## 本地开发

```bash
make install-dev
make verify      # ruff + pytest
make run         # 零依赖本地启动
```

## 提交前

- `make verify` 必须通过。
- 新增评测指标/参数时，同步更新 `docs/evaluation.md` 和 README 的评测表。
- 不提交 `.env`、`data/`、`uploads/`（已在 `.gitignore`）。
- 评测数据集放 `data/eval/`，只放行 `golden.json`、`golden_generation.json`、`agent_tasks.json`。

## 代码风格

- ruff 当前只启用 `E9/F`（语法与未定义名），后续可逐步加 `I`/`B`。
- 测试只覆盖纯函数和聚合逻辑，不依赖外部服务/API Key。
