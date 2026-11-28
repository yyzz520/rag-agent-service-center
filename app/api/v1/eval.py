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
from sqlalchemy.orm import Session

from app.api.responses import error, ok
from app.core.rbac import require_permission
from app.core.security import UserContext
from app.db import get_db
from app.rag.agent_eval import read_agent_reports, run_agent_eval
from app.rag.diagnosis import run_diagnosis
from app.rag.eval import read_reports, run_eval
from app.rag.experiments import EXPERIMENTS, read_experiments, run_experiment
from app.rag.gen_eval import read_gen_reports, run_gen_eval
from app.rag.plan_eval import read_plan_reports, run_plan_eval
from app.rag.router_eval import read_router_reports, run_router_eval
from app.services.audit import record as audit_record

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


class ExperimentRequest(BaseModel):
    name: str = Field(description=f"实验名：{', '.join(EXPERIMENTS)}")
    note: str | None = Field(default=None, description="给这次运行打个标签")


class RouterEvalRequest(BaseModel):
    note: str | None = Field(default=None, description="给这次运行打个标签，便于和历史里的其他运行区分")


class PlanEvalRequest(BaseModel):
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


@router.get("/plan", summary="查看 Plan-and-Execute 评测历史（仅管理员）")
def list_plan_runs(_: UserContext = Depends(require_permission("eval:read"))):
    return ok(_history(read_plan_reports()))


@router.post("/plan", summary="跑一轮 Plan-and-Execute 评测（仅管理员；真调 LLM、有花费）")
def run_plan_now(
    req: PlanEvalRequest = PlanEvalRequest(),
    user: UserContext = Depends(require_permission("eval:run")),
    db: Session = Depends(get_db),
):
    try:
        report = run_plan_eval(note=req.note)
    except ValueError as e:
        return error(40001, str(e))
    except Exception as e:
        return error(50001, f"计划评测失败：{e}", status=500)
    audit_record(db, user, "eval.plan", req.note or "",
                 f"success_rate={report['metrics'].get('success_rate')}")
    return ok(report)


@router.get("/router", summary="查看路由评测历史（仅管理员）")
def list_router_runs(_: UserContext = Depends(require_permission("eval:read"))):
    return ok(_history(read_router_reports()))


@router.post("/router", summary="跑一轮路由评测（仅管理员；规则分类，不调模型）")
def run_router_now(
    req: RouterEvalRequest = RouterEvalRequest(),
    user: UserContext = Depends(require_permission("eval:run")),
    db: Session = Depends(get_db),
):
    try:
        report = run_router_eval(note=req.note)
    except ValueError as e:
        return error(40001, str(e))
    except Exception as e:
        return error(50001, f"路由评测失败：{e}", status=500)
    audit_record(db, user, "eval.router", req.note or "", f"accuracy={report['metrics'].get('accuracy')}")
    return ok(report)


@router.get("/experiments", summary="查看单变量 A/B 实验历史（仅管理员）")
def list_experiments(_: UserContext = Depends(require_permission("eval:read"))):
    return ok(_history(read_experiments()))


@router.post("/experiments", summary="跑一轮单变量 A/B 实验（仅管理员；部分实验真调 LLM）")
def run_experiment_now(
    req: ExperimentRequest,
    user: UserContext = Depends(require_permission("eval:run")),
    db: Session = Depends(get_db),
):
    try:
        report = run_experiment(req.name, note=req.note)
    except ValueError as e:
        return error(40001, str(e))
    except Exception as e:
        return error(50001, f"实验失败：{e}", status=500)
    audit_record(db, user, "eval.experiment", req.name, f"comparable={report.get('comparable')}")
    return ok(report)


@router.get("/retrieval", summary="查看检索评测历史（仅管理员）")
def list_retrieval_runs(_: UserContext = Depends(require_permission("eval:read"))):
    return ok(_history(read_reports()))


@router.post("/retrieval", summary="跑一轮检索评测（仅管理员；不调生成模型）")
def run_retrieval_now(
    req: RetrievalEvalRequest = RetrievalEvalRequest(),
    user: UserContext = Depends(require_permission("eval:run")),
    db: Session = Depends(get_db),
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
    audit_record(db, user, "eval.retrieval", req.note or "", f"total={report['metrics'].get('total')}")
    return ok(report)


@router.get("/generation", summary="查看生成评测历史（仅管理员）")
def list_generation_runs(_: UserContext = Depends(require_permission("eval:read"))):
    return ok(_history(read_gen_reports()))


@router.post("/generation", summary="跑一轮生成评测（仅管理员；真调 LLM）")
def run_generation_now(
    req: GenerationEvalRequest = GenerationEvalRequest(),
    user: UserContext = Depends(require_permission("eval:run")),
    db: Session = Depends(get_db),
):
    try:
        report = run_gen_eval(note=req.note)
    except ValueError as e:
        return error(40001, str(e))
    except Exception as e:
        return error(50001, f"生成评测失败：{e}", status=500)
    audit_record(db, user, "eval.generation", req.note or "", f"total={report['metrics'].get('total')}")
    return ok(report)


@router.get("/agent", summary="查看 Agent 行为评测历史（仅管理员）")
def list_agent_runs(_: UserContext = Depends(require_permission("eval:read"))):
    return ok(_history(read_agent_reports()))


@router.post("/agent", summary="跑一轮 Agent 行为评测（仅管理员；真调 LLM）")
def run_agent_now(
    req: AgentEvalRequest = AgentEvalRequest(),
    user: UserContext = Depends(require_permission("eval:run")),
    db: Session = Depends(get_db),
):
    try:
        report = run_agent_eval(note=req.note)
    except ValueError as e:
        return error(40001, str(e))
    except Exception as e:
        return error(50001, f"Agent 评测失败：{e}", status=500)
    audit_record(db, user, "eval.agent", req.note or "", f"tasks={report['metrics'].get('tasks')}")
    return ok(report)


@router.get("/summary", summary="三层评测最新指标汇总（仅管理员）")
def latest_summary(_: UserContext = Depends(require_permission("eval:read"))):
    retrieval = read_reports()
    generation = read_gen_reports()
    agent = read_agent_reports()
    return ok({
        "retrieval": retrieval[-1].get("metrics") if retrieval else None,
        "generation": generation[-1].get("metrics") if generation else None,
        "agent": agent[-1].get("metrics") if agent else None,
    })


@router.get("/diagnosis", summary="对最近一次评测做规则化 badcase 归因（仅管理员）")
def diagnosis(_: UserContext = Depends(require_permission("eval:read"))):
    return ok(run_diagnosis())
