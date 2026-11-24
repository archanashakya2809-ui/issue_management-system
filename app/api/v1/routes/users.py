from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, require_permission
from app.models.user import User
from app.schemas.user import UserListResponse
from app.schemas.user_preferences import UserPreferencesRead, UserPreferencesUpdate
from app.services.user_service import list_users
from app.services.user_preferences_service import get_user_preferences, update_user_preferences

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=UserListResponse)
def read_users(
    current_user: Annotated[User, Depends(require_permission("user.view"))],
    db: DbSession,
    department_id: UUID | None = Query(default=None),
    team_id: UUID | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> UserListResponse:
    """List users visible to the caller, scoped by RLS and optional filters."""
    return list_users(
        db,
        company_id=current_user.company_id,
        department_id=department_id,
        team_id=team_id,
        q=q,
        limit=limit,
        offset=offset,
    )

@router.get("/me/preferences", response_model=UserPreferencesRead)
def read_my_preferences(
    current_user: CurrentUser,
    db: DbSession,
) -> UserPreferencesRead:
    """Read only the authenticated user's personal preferences."""
    return get_user_preferences(
        db, company_id=current_user.company_id, user_id=current_user.id
    )


@router.put("/me/preferences", response_model=UserPreferencesRead)
def save_my_preferences(
    payload: UserPreferencesUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> UserPreferencesRead:
    """Save only the authenticated user's personal preferences."""
    return update_user_preferences(
        db,
        company_id=current_user.company_id,
        user_id=current_user.id,
        payload=payload,
    )

