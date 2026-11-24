from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import DbSession, require_permission
from app.models.user import User
from app.schemas.comment import CommentCreate, CommentRead
from app.services.permission_service import user_has_permission
from app.services.ticket_service import create_comment, get_ticket, list_comments

router = APIRouter(prefix="/tickets/{ticket_id}/comments", tags=["ticket comments"])


@router.get("", response_model=list[CommentRead])
def read_comments(
    ticket_id: UUID,
    current_user: Annotated[User, Depends(require_permission("ticket.view"))],
    db: DbSession,
) -> list[CommentRead]:
    get_ticket(db, company_id=current_user.company_id, ticket_id=ticket_id)
    comments = list_comments(db, company_id=current_user.company_id, ticket_id=ticket_id)
    return [CommentRead.model_validate(comment) for comment in comments]


@router.post("", response_model=CommentRead, status_code=status.HTTP_201_CREATED)
def add_comment(
    ticket_id: UUID,
    payload: CommentCreate,
    current_user: Annotated[User, Depends(require_permission("ticket.comment"))],
    db: DbSession,
) -> CommentRead:
    ticket = get_ticket(db, company_id=current_user.company_id, ticket_id=ticket_id)
    if payload.is_internal and not user_has_permission(
        db, user_id=current_user.id, company_id=current_user.company_id,
        permission_code="ticket.internal_note"
    ):
        raise HTTPException(status_code=403, detail="Permission required: ticket.internal_note")
    comment = create_comment(db, current_user=current_user, ticket=ticket, payload=payload)
    return CommentRead.model_validate(comment)
