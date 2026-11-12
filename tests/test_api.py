import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import app.db as db_module
import app.main as main_module
from app.agent import parse_analysis

AUTH = {"X-Webhook-Token": "test-token"}


def firing(fp="f1", name="HighCPU"):
    return {
        "alerts": [
            {
                "status": "firing",
                "fingerprint": fp,
                "labels": {"alertname": name, "severity": "critical"},
                "annotations": {"summary": "CPU high"},
            }
        ]
    }


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    monkeypatch.setattr(main_module, "get_engine", lambda: engine)
    monkeypatch.setattr(
        main_module,
        "run_analysis",
        lambda *a, **k: {
            "evidence": "e",
            "root_cause": "stub cause",
            "remediation": "stub fix",
        },
    )
    db_module.Base.metadata.create_all(engine)
    return TestClient(main_module.app)


def test_health_is_open(client):
    assert client.get("/health").status_code == 200


def test_webhook_requires_token(client):
    assert client.post("/webhook/alertmanager", json=firing()).status_code == 401


def test_bearer_token_accepted(client):
    r = client.post(
        "/webhook/alertmanager",
        json=firing(),
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 200


def test_duplicate_alert_ignored(client):
    first = client.post("/webhook/alertmanager", json=firing(), headers=AUTH)
    second = client.post("/webhook/alertmanager", json=firing(), headers=AUTH)
    assert first.json() == {"accepted": [1]}
    assert second.json() == {"accepted": []}


def test_analysis_saved(client):
    client.post("/webhook/alertmanager", json=firing(), headers=AUTH)
    incident = client.get("/incidents/1", headers=AUTH).json()
    assert incident["root_cause"] == "stub cause"


def test_resolved_closes_incident(client):
    client.post("/webhook/alertmanager", json=firing(), headers=AUTH)
    resolved = firing()
    resolved["alerts"][0]["status"] = "resolved"
    client.post("/webhook/alertmanager", json=resolved, headers=AUTH)
    assert client.get("/incidents/1", headers=AUTH).json()["status"] == "resolved"


def test_unknown_incident_404(client):
    assert client.get("/incidents/99", headers=AUTH).status_code == 404


def test_parse_analysis_splits_sections():
    cause, fix = parse_analysis("ROOT CAUSE: a\nREMEDIATION: 1. b")
    assert cause == "a"
    assert fix == "1. b"


def test_parse_analysis_fallback():
    cause, fix = parse_analysis("just some text")
    assert cause == "just some text"
    assert fix == "Not provided by the model."
