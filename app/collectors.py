import httpx

from app.config import get_settings

HTTP_TIMEOUT = 10


def _clean(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum() or ch in "-_.")


def query_prometheus(promql: str) -> str:
    settings = get_settings()
    try:
        response = httpx.get(
            f"{settings.prometheus_url}/api/v1/query",
            params={"query": promql},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json()["data"]["result"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return f"prometheus unavailable: {exc}"
    if not results:
        return "no data"
    return "\n".join(f"{r['metric']} => {r['value'][1]}" for r in results[:10])


def collect_metrics(labels: dict) -> str:
    job = _clean(labels.get("job", ""))
    selector = f'{{job="{job}"}}' if job else ""
    queries = {
        "up": f"up{selector}",
        "cpu_rate_5m": f"rate(process_cpu_seconds_total{selector}[5m])",
        "resident_memory_bytes": f"process_resident_memory_bytes{selector}",
    }
    return "\n".join(f"[{name}]\n{query_prometheus(q)}" for name, q in queries.items())


def collect_logs(labels: dict, limit: int = 30) -> str:
    settings = get_settings()
    service = _clean(labels.get("service") or labels.get("job") or "")
    selector = (
        f'{{compose_service="{service}"}}' if service else '{compose_service=~".+"}'
    )
    try:
        response = httpx.get(
            f"{settings.loki_url}/loki/api/v1/query_range",
            params={"query": selector, "limit": limit, "direction": "backward"},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        streams = response.json()["data"]["result"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return f"loki unavailable: {exc}"
    lines = [entry[1] for stream in streams for entry in stream["values"]]
    if not lines:
        return "no log lines"
    return "\n".join(lines[:limit])