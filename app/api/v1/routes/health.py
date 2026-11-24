import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import DbSession
from app.core.config import settings
from app.db.session import get_db
from app.schemas.health import HealthIncidentIngest, HealthIncidentIngestResponse

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe: the API process is responding."""
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(db: Session = Depends(get_db)) -> dict[str, str]:
    """Readiness probe: PostgreSQL can execute a simple query."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Database is not ready") from exc


@router.post(
    "/health/incidents",
    response_model=HealthIncidentIngestResponse,
    tags=["health-monitoring"],
)
def ingest_health_incident(
    payload: HealthIncidentIngest,
    db: DbSession,
    x_health_monitor_token: Annotated[
        str | None, Header(alias="X-Health-Monitor-Token")
    ] = None,
) -> HealthIncidentIngestResponse:
    """Accept a health state transition from a trusted external monitor.

    This endpoint intentionally uses a dedicated shared secret instead of an
    interactive System Admin bearer token. The secret must be configured in
    HEALTH_INGEST_TOKEN and must be at least 32 characters.
    """
    configured_token = settings.health_ingest_token
    if len(configured_token) < 32:
        raise HTTPException(
            status_code=503,
            detail="Health incident ingestion is not configured.",
        )

    if not x_health_monitor_token or not hmac.compare_digest(
        x_health_monitor_token, configured_token
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid health monitor credentials.",
            headers={"WWW-Authenticate": "HealthMonitor"},
        )

    try:
        created = db.execute(
            text(
                """
                SELECT public.emit_system_admin_health_notification(
                    :service, :state, :incident_key, :summary, :source_company_id
                )
                """
            ),
            {
                "service": payload.service,
                "state": payload.state,
                "incident_key": payload.incident_key,
                "summary": payload.summary,
                "source_company_id": payload.source_company_id,
            },
        ).scalar_one()
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Unable to persist the health incident.",
        ) from exc

    return HealthIncidentIngestResponse(
        notifications_created=int(created or 0),
        service=payload.service,
        state=payload.state,
        incident_key=payload.incident_key,
    )
