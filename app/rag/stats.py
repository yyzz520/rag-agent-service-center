"""小样本评测的统计口径：bootstrap 置信区间 + 多次运行的波动。

面试口径：61/20/8 是验证集，不是统计样本。点估计之外给出 95% 区间，
同题多跑再给 min/max/std——避免把一次运行的 0.917 说成"稳定在 91.7%"。
"""
import random
import statistics


def bootstrap_ci(values: list[float], n: int = 1000, seed: int = 42, level: float = 0.95) -> dict:
    """对逐题得分做 bootstrap 重采样，返回 {mean, lo, hi}（固定种子，可复现）。"""
    if not values:
        return {"mean": 0.0, "lo": 0.0, "hi": 0.0}
    rng = random.Random(seed)
    k = len(values)
    means = sorted(
        sum(values[rng.randrange(k)] for _ in range(k)) / k for _ in range(n)
    )
    alpha = (1 - level) / 2
    return {
        "mean": round(sum(values) / k, 3),
        "lo": round(means[int(alpha * (n - 1))], 3),
        "hi": round(means[int((1 - alpha) * (n - 1))], 3),
    }


def stability(run_values: list[float]) -> dict:
    """多次运行的指标波动：同一组题跑 N 轮，看 min/max/std。"""
    if not run_values:
        return {}
    return {
        "runs": len(run_values),
        "mean": round(sum(run_values) / len(run_values), 3),
        "min": round(min(run_values), 3),
        "max": round(max(run_values), 3),
        "std": round(statistics.pstdev(run_values), 3),
    }
