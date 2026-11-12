from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from app.collectors import collect_logs, collect_metrics
from app.config import get_settings


class IncidentState(TypedDict, total=False):
    alertname: str
    severity: str
    labels: dict
    annotations: dict
    evidence: str
    root_cause: str
    remediation: str


PROMPT = """You are an expert site reliability engineer handling a live incident.

Alert: {alertname}
Severity: {severity}
Labels: {labels}
Annotations: {annotations}

Evidence collected automatically:
{evidence}

Rules:
- "no data" or "unavailable" in the evidence means the data could not be collected. It is NOT the cause of the incident. Never blame Prometheus, Loki or any monitoring tool for the alert.
- Only name a cause that the evidence or the alert text actually supports.
- If the evidence does not show a cause, write that the root cause is undetermined from the available evidence, and give the most common causes for this kind of alert as things to check.
- cpu_rate_5m is CPU cores in use: 0.01 means 1 percent of one core, 0.9 means 90 percent. Compare it with any CPU figure in the alert text.
- If a measured value is far from what the alert claims, the first sentence of ROOT CAUSE must start with "The evidence contradicts the alert:" and give both numbers, then say the alert is likely a false positive or stale.

Reply in exactly this format and nothing else:
ROOT CAUSE: 2-3 sentences.
REMEDIATION: 3 short numbered steps an on-call engineer should take now.
"""


def ask_llm(prompt: str) -> str:
    settings = get_settings()
    try:
        response = httpx.post(
            f"{settings.ollama_url}/api/generate",
            json={"model": settings.ollama_model, "prompt": prompt, "stream": False},
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()["response"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return f"LLM unavailable: {exc}"


def parse_analysis(text: str) -> tuple[str, str]:
    upper = text.upper()
    cause_at = upper.find("ROOT CAUSE")
    fix_at = upper.find("REMEDIATION")
    if cause_at == -1 or fix_at == -1 or fix_at < cause_at:
        return text.strip(), "Not provided by the model."
    cause = text[cause_at + len("ROOT CAUSE") : fix_at].strip(" :*-\n")
    fix = text[fix_at + len("REMEDIATION") :].strip(" :*-\n")
    return cause, fix


def gather_evidence(state: IncidentState) -> dict:
    labels = state.get("labels", {})
    metrics = collect_metrics(labels)
    logs = collect_logs(labels)
    return {"evidence": f"METRICS:\n{metrics}\n\nLOGS:\n{logs}"}


def analyze(state: IncidentState) -> dict:
    prompt = PROMPT.format(
        alertname=state.get("alertname", "unknown"),
        severity=state.get("severity", "unknown"),
        labels=state.get("labels", {}),
        annotations=state.get("annotations", {}),
        evidence=state.get("evidence", ""),
    )
    cause, fix = parse_analysis(ask_llm(prompt))
    return {"root_cause": cause, "remediation": fix}


def build_graph():
    graph = StateGraph(IncidentState)
    graph.add_node("gather_evidence", gather_evidence)
    graph.add_node("analyze", analyze)
    graph.add_edge(START, "gather_evidence")
    graph.add_edge("gather_evidence", "analyze")
    graph.add_edge("analyze", END)
    return graph.compile()


def run_analysis(
    alertname: str, severity: str, labels: dict, annotations: dict
) -> IncidentState:
    return build_graph().invoke(
        {
            "alertname": alertname,
            "severity": severity,
            "labels": labels,
            "annotations": annotations,
        }
    )