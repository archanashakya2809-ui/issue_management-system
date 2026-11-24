from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser, DbSession, require_permission
from app.models.user import User
from app.schemas.attachment import TicketAttachmentRead
from app.services.attachment_service import (
    get_attachment_file,
    list_ticket_attachments,
    upload_ticket_attachments,
)

router = APIRouter(
    prefix="/tickets/{ticket_id}/attachments",
    tags=["ticket attachments"],
)


@router.get("", response_model=list[TicketAttachmentRead])
def read_ticket_attachments(
    ticket_id: UUID,
    current_user: Annotated[User, Depends(require_permission("ticket.view"))],
    db: DbSession,
) -> list[TicketAttachmentRead]:
    rows = list_ticket_attachments(
        db, current_user=current_user, ticket_id=ticket_id
    )
    return [TicketAttachmentRead.model_validate(row) for row in rows]


@router.post(
    "",
    response_model=list[TicketAttachmentRead],
    status_code=status.HTTP_201_CREATED,
)
async def upload_ticket_attachments_route(
    ticket_id: UUID,
    current_user: Annotated[User, Depends(require_permission("attachment.upload"))],
    db: DbSession,
    files: Annotated[list[UploadFile], File(description="One or more ticket attachments")],
) -> list[TicketAttachmentRead]:
    rows = await upload_ticket_attachments(
        db,
        current_user=current_user,
        ticket_id=ticket_id,
        uploads=files,
    )
    return [TicketAttachmentRead.model_validate(row) for row in rows]


@router.get("/{attachment_id}/download", response_class=FileResponse)
def download_ticket_attachment(
    ticket_id: UUID,
    attachment_id: UUID,
    current_user: Annotated[User, Depends(require_permission("ticket.view"))],
    db: DbSession,
) -> FileResponse:
    attachment, path = get_attachment_file(
        db,
        current_user=current_user,
        ticket_id=ticket_id,
        attachment_id=attachment_id,
    )
    return FileResponse(
        path=path,
        filename=attachment["original_filename"],
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
