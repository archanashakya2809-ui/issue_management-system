from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.notification import (
    MarkAllNotificationsReadResponse,
    NotificationRead,
)
from app.services.notification_service import (
    list_notifications,
    mark_all_notifications_read,
    mark_notification_read,
    refresh_operational_notifications,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationRead], response_model_exclude_none=True)
def read_notifications(
    current_user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[NotificationRead]:
    """List notifications addressed to the authenticated user."""
    refresh_operational_notifications(
        db,
        recipient_user_id=current_user.id,
    )
    rows = list_notifications(
        db,
        company_id=current_user.company_id,
        recipient_user_id=current_user.id,
        limit=limit,
        offset=offset,
    )
    return [NotificationRead.model_validate(row) for row in rows]


@router.patch("/read-all", response_model=MarkAllNotificationsReadResponse)
def read_all_notifications(
    current_user: CurrentUser,
    db: DbSession,
) -> MarkAllNotificationsReadResponse:
    """Mark every unread notification addressed to the caller as read."""
    updated = mark_all_notifications_read(
        db,
        company_id=current_user.company_id,
        recipient_user_id=current_user.id,
    )
    return MarkAllNotificationsReadResponse(updated=updated)


@router.patch("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def read_notification(
    notification_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> Response:
    """Mark one notification as read; foreign notification IDs return 404."""
    mark_notification_read(
        db,
        company_id=current_user.company_id,
        recipient_user_id=current_user.id,
        notification_id=notification_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
