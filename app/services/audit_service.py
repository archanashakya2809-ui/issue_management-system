import json
import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def write_audit_event(
    db: Session,
    *,
    company_id: UUID,
    action: str,
    entity_type: str,
    entity_id: UUID | None,
    metadata: dict | None = None,
) -> None:
    """Use the database's audit helper; a failure rolls back the business transaction."""
    try:
        db.execute(
            text(
                "SELECT public.app_write_audit("
                ":company_id, :action, :entity_type, :entity_id, CAST(:metadata AS jsonb))"
            ),
            {
                "company_id": company_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "metadata": json.dumps(metadata or {}),
            },
        )
    except Exception:
        logger.exception("Database audit helper failed")
        raise
