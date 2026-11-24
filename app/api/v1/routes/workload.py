from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, require_permission
from app.models.user import User
from app.schemas.workload import TeamWorkloadItem
from app.services.workload_service import get_workload

router = APIRouter(prefix="/workload", tags=["workload"])


@router.get("", response_model=list[TeamWorkloadItem])
def read_workload(
    current_user: Annotated[User, Depends(require_permission("report.view"))],
    db: DbSession,
    department_id: UUID | None = Query(default=None),
) -> list[TeamWorkloadItem]:
    """Per-team workload summary: active ticket counts and SLA breach counts."""
    return get_workload(
        db,
        company_id=current_user.company_id,
        department_id=department_id,
    )
