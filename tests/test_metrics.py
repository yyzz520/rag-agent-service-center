from app.core import metrics


def setup_function():
    metrics.reset_metrics()


def test_normalize_path_replaces_ids():
    assert (
        metrics.normalize_path("/api/v1/docs/7177c281e9fd4a98ab1b2b9ee67ecde2")
        == "/api/v1/docs/{id}"
    )
    assert metrics.normalize_path("/api/v1/sessions/123") == "/api/v1/sessions/{id}"
    assert metrics.normalize_path("/api/v1/rag/chat") == "/api/v1/rag/chat"


def test_render_prometheus_counts_requests_and_duration():
    metrics.record_request(
        "GET", "/api/v1/docs/7177c281e9fd4a98ab1b2b9ee67ecde2", 200, 0.1
    )
    metrics.record_request(
        "GET", "/api/v1/docs/7177c281e9fd4a98ab1b2b9ee67ecde2", 200, 0.2
    )
    text = metrics.render_prometheus()
    assert (
        'http_requests_total{method="GET",path="/api/v1/docs/{id}",status="200"} 2'
        in text
    )
    assert (
        'http_request_duration_seconds_count{method="GET",path="/api/v1/docs/{id}"} 2'
        in text
    )
    assert "process_uptime_seconds" in text
