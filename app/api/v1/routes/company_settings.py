from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, require_permission
from app.models.user import User
from app.schemas.company_settings import CompanySettingsRead, CompanySettingsUpdate
from app.services.company_settings_service import (
    get_company_settings,
    update_company_settings,
)

router = APIRouter(prefix="/company", tags=["company-settings"])


@router.get("/settings", response_model=CompanySettingsRead)
def read_company_settings(
    current_user: Annotated[User, Depends(require_permission("company.settings.manage"))],
    db: DbSession,
) -> CompanySettingsRead:
    """Return settings for the authenticated user's company only."""
    return get_company_settings(db, company_id=current_user.company_id)


@router.put("/settings", response_model=CompanySettingsRead)
def save_company_settings(
    payload: CompanySettingsUpdate,
    current_user: Annotated[User, Depends(require_permission("company.settings.manage"))],
    db: DbSession,
) -> CompanySettingsRead:
    """Update settings for the authenticated user's company only."""
    return update_company_settings(db, current_user=current_user, payload=payload)
