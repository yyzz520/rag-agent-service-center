"""评测 API：检索 / 生成 / Agent 三层评测。

评测是横切能力，不挂在 /rag 或 /agent 业务 router 下：
- /eval/retrieval  检索评测（recall@k / MRR）
- /eval/generation 生成评测（正确率 / 引用率 / 拒答率）
- /eval/agent      Agent 行为评测（工具路由 / 完成率 / 步数 / token）
- /eval/summary    三层最新指标汇总

全部仅管理员可调；旧 /rag/*-eval 路径保留为隐藏兼容别名。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.responses import error, ok
from app.core.security import UserContext, require_admin
from app.rag.agent_eval import read_agent_reports, run_agent_eval
from app.rag.eval import read_reports, run_eval
from app.rag.gen_eval import read_gen_reports, run_gen_eval

router = APIRouter(prefix="/eval", tags=["评测"])


class RetrievalEvalRequest(BaseModel):
    note: str | None = Field(default=None, description="给这次运行打个标签，便于和历史里的其他运行区分")
    candidate_pool: int | None = Field(default=None, ge=1)
    per_query_top: int | None = Field(default=None, ge=1)
    max_docs: int | None = Field(default=None, ge=1)
    use_rerank: bool | None = Field(default=None)


class GenerationEvalRequest(BaseModel):
    note: str | None = Field(default=None, description="给这次运行打个标签，便于和历史里的其他运行区分")


class AgentEvalRequest(BaseModel):
    note: str | None = Field(default=None, description="给这次运行打个标签，便于和历史里的其他运行区分")


def _history(runs: list[dict]) -> dict:
    return {
        "total": len(runs),
        "latest": runs[-1] if runs else None,
        "summary": [
            {"run": i, "note": r.get("note"), "metrics": r.get("metrics")}
            for i, r in enumerate(runs, start=1)
        ],
    }


@router.get("/retrieval", summary="查看检索评测历史（仅管理员）")
def list_retrieval_runs(_: UserContext = Depends(require_admin)):
    return ok(_history(read_reports()))


@router.post("/retrieval", summary="跑一轮检索评测（仅管理员；不调生成模型）")
def run_retrieval_now(
    req: RetrievalEvalRequest = RetrievalEvalRequest(),
    _: UserContext = Depends(require_admin),
):
    overrides = {
        "candidate_pool": req.candidate_pool,
        "per_query_top": req.per_query_top,
        "max_docs": req.max_docs,
        "use_rerank": req.use_rerank,
    }
    try:
        report = run_eval(note=req.note, overrides=overrides)
    except ValueError as e:
        return error(40001, str(e))
    except Exception as e:
        return error(50001, f"检索评测失败：{e}", status=500)
    return ok(report)


@router.get("/generation", summary="查看生成评测历史（仅管理员）")
def list_generation_runs(_: UserContext = Depends(require_admin)):
    return ok(_history(read_gen_reports()))


@router.post("/generation", summary="跑一轮生成评测（仅管理员；真调 LLM）")
def run_generation_now(
    req: GenerationEvalRequest = GenerationEvalRequest(),
    _: UserContext = Depends(require_admin),
):
    try:
        report = run_gen_eval(note=req.note)
    except ValueError as e:
        return error(40001, str(e))
    except Exception as e:
        return error(50001, f"生成评测失败：{e}", status=500)
    return ok(report)


@router.get("/agent", summary="查看 Agent 行为评测历史（仅管理员）")
def list_agent_runs(_: UserContext = Depends(require_admin)):
    return ok(_history(read_agent_reports()))


@router.post("/agent", summary="跑一轮 Agent 行为评测（仅管理员；真调 LLM）")
def run_agent_now(
    req: AgentEvalRequest = AgentEvalRequest(),
    _: UserContext = Depends(require_admin),
):
    try:
        report = run_agent_eval(note=req.note)
    except ValueError as e:
        return error(40001, str(e))
    except Exception as e:
        return error(50001, f"Agent 评测失败：{e}", status=500)
    return ok(report)


@router.get("/summary", summary="三层评测最新指标汇总（仅管理员）")
def latest_summary(_: UserContext = Depends(require_admin)):
    retrieval = read_reports()
    generation = read_gen_reports()
    agent = read_agent_reports()
    return ok({
        "retrieval": retrieval[-1].get("metrics") if retrieval else None,
        "generation": generation[-1].get("metrics") if generation else None,
        "agent": agent[-1].get("metrics") if agent else None,
    })
