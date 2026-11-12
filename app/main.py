import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import run_analysis
from app.config import get_settings
from app.db import Incident, get_db, get_engine, init_db

INCIDENTS_RECEIVED = Counter(
    "incidents_received_total", "Firing alerts accepted", ["severity"]
)
ANALYSES_COMPLETED = Counter("incident_analyses_total", "Completed analyses")
ANALYSIS_SECONDS = Histogram(
    "incident_analysis_seconds", "Time spent analysing one incident"
)


class Alert(BaseModel):
    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime | None = None
    fingerprint: str = ""


class AlertmanagerPayload(BaseModel):
    alerts: list[Alert] = Field(default_factory=list)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI Incident Response Agent", lifespan=lifespan)
DbSession = Annotated[Session, Depends(get_db)]


def require_token(
    x_webhook_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    supplied = x_webhook_token
    if supplied is None and authorization and authorization.lower().startswith(
        "bearer "
    ):
        supplied = authorization[7:]
    expected = get_settings().webhook_token
    if not supplied or not secrets.compare_digest(
        supplied.encode(), expected.encode()
    ):
        raise HTTPException(status_code=401, detail="invalid or missing token")


def serialize(incident: Incident, with_evidence: bool = False) -> dict:
    data = {
        "id": incident.id,
        "alertname": incident.alertname,
        "severity": incident.severity,
        "status": incident.status,
        "root_cause": incident.root_cause,
        "remediation": incident.remediation,
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
    }
    if with_evidence:
        data["labels"] = incident.labels
        data["annotations"] = incident.annotations
        data["evidence"] = incident.evidence
    return data


def analyze_incident(incident_id: int) -> None:
    with Session(get_engine()) as db:
        incident = db.get(Incident, incident_id)
        if incident is None:
            return
        with ANALYSIS_SECONDS.time():
            result = run_analysis(
                incident.alertname,
                incident.severity,
                incident.labels,
                incident.annotations,
            )
        incident.evidence = result.get("evidence", "")
        incident.root_cause = result.get("root_cause", "")
        incident.remediation = result.get("remediation", "")
        db.commit()
        ANALYSES_COMPLETED.inc()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/webhook/alertmanager", dependencies=[Depends(require_token)])
def alertmanager_webhook(
    payload: AlertmanagerPayload,
    background: BackgroundTasks,
    db: DbSession,
) -> dict:
    accepted = []
    for alert in payload.alerts:
        open_incidents = list(
            db.scalars(
                select(Incident).where(
                    Incident.fingerprint == alert.fingerprint,
                    Incident.status == "firing",
                )
            )
        )
        if alert.status == "resolved":
            for open_incident in open_incidents:
                open_incident.status = "resolved"
            db.commit()
            continue
        if open_incidents:
            continue
        incident = Incident(
            fingerprint=alert.fingerprint,
            alertname=alert.labels.get("alertname", "unknown"),
            severity=alert.labels.get("severity", "unknown"),
            status="firing",
            labels=alert.labels,
            annotations=alert.annotations,
            starts_at=alert.startsAt,
        )
        db.add(incident)
        db.commit()
        db.refresh(incident)
        INCIDENTS_RECEIVED.labels(severity=incident.severity).inc()
        background.add_task(analyze_incident, incident.id)
        accepted.append(incident.id)
    return {"accepted": accepted}


@app.get("/incidents", dependencies=[Depends(require_token)])
def list_incidents(db: DbSession, limit: int = 50) -> list[dict]:
    rows = db.scalars(
        select(Incident).order_by(Incident.id.desc()).limit(min(limit, 200))
    )
    return [serialize(row) for row in rows]


@app.get("/incidents/{incident_id}", dependencies=[Depends(require_token)])
def get_incident(incident_id: int, db: DbSession) -> dict:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return serialize(incident, with_evidence=True)