"""轻量 Prometheus 指标：HTTP 请求计数 + 延迟 sum/count + 进程 uptime。

不引入 prometheus_client：输出格式固定、依赖少，够本地/内网抓取。
路由里带 uuid/数字的路径会归一化成 `{id}`，避免高基数把指标打爆。
"""
import re
import threading
import time

_start_time = time.time()
_lock = threading.Lock()
_requests: dict[tuple[str, str, str], int] = {}
_duration_sum: dict[tuple[str, str], float] = {}
_duration_count: dict[tuple[str, str], int] = {}

_ID_SEGMENT = re.compile(r"/(?:[0-9a-fA-F]{8,}|\d+)(?=/|$)")


def normalize_path(path: str) -> str:
    """把 /api/v1/docs/<uuid> 归一化成 /api/v1/docs/{id}，控制标签基数。"""
    return _ID_SEGMENT.sub("/{id}", path)


def record_request(method: str, path: str, status: int, duration_seconds: float) -> None:
    path = normalize_path(path)
    method = method.upper()
    status_s = str(status)
    with _lock:
        key = (method, path, status_s)
        _requests[key] = _requests.get(key, 0) + 1
        dkey = (method, path)
        _duration_sum[dkey] = _duration_sum.get(dkey, 0.0) + float(duration_seconds)
        _duration_count[dkey] = _duration_count.get(dkey, 0) + 1


def reset_metrics() -> None:
    with _lock:
        _requests.clear()
        _duration_sum.clear()
        _duration_count.clear()


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_prometheus() -> str:
    with _lock:
        requests = dict(_requests)
        dur_sum = dict(_duration_sum)
        dur_count = dict(_duration_count)

    lines = [
        "# HELP http_requests_total Total HTTP requests by method/path/status.",
        "# TYPE http_requests_total counter",
    ]
    for (method, path, status), count in sorted(requests.items()):
        lines.append(
            f'http_requests_total{{method="{_esc(method)}",path="{_esc(path)}",status="{_esc(status)}"}} {count}'
        )

    lines += [
        "# HELP http_request_duration_seconds_sum Total request duration in seconds.",
        "# TYPE http_request_duration_seconds summary",
    ]
    for (method, path), total in sorted(dur_sum.items()):
        lines.append(
            f'http_request_duration_seconds_sum{{method="{_esc(method)}",path="{_esc(path)}"}} {total:.6f}'
        )
        lines.append(
            f'http_request_duration_seconds_count{{method="{_esc(method)}",path="{_esc(path)}"}} {dur_count.get((method, path), 0)}'
        )

    lines += [
        "# HELP process_uptime_seconds Process uptime in seconds.",
        "# TYPE process_uptime_seconds gauge",
        f"process_uptime_seconds {time.time() - _start_time:.3f}",
    ]
    return "\n".join(lines) + "\n"
