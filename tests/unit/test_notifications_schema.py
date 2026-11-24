from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.schemas.notification import NotificationRead


def test_notification_schema_serializes_frontend_contract() -> None:
    notification_id = uuid4()
    ticket_id = uuid4()
    created_at = datetime.now(UTC)
    row = SimpleNamespace(
        id=notification_id,
        type="ticket_assigned",
        title="Ticket assigned to your team",
        message="A ticket has been assigned to your team.",
        created_at=created_at,
        is_read=False,
        ticket_id=ticket_id,
    )

    payload = NotificationRead.model_validate(row).model_dump(by_alias=True, exclude_none=True)

    assert payload == {
        "id": notification_id,
        "type": "ticket_assigned",
        "title": "Ticket assigned to your team",
        "message": "A ticket has been assigned to your team.",
        "createdAt": created_at,
        "read": False,
        "ticketId": ticket_id,
    }


def test_notification_schema_allows_notification_without_ticket() -> None:
    row = SimpleNamespace(
        id=uuid4(),
        type="system_notice",
        title="Maintenance notice",
        message="Scheduled maintenance is planned.",
        created_at=datetime.now(UTC),
        is_read=True,
        ticket_id=None,
    )

    payload = NotificationRead.model_validate(row).model_dump(by_alias=True)

    assert payload["ticketId"] is None
    assert payload["read"] is True


def test_notification_schema_serializes_generic_entity_reference() -> None:
    notification_id = uuid4()
    entity_id = uuid4()
    row = SimpleNamespace(
        id=notification_id,
        type="company_team_created",
        title="Team created",
        message="A team was created.",
        created_at=datetime.now(UTC),
        is_read=False,
        ticket_id=None,
        entity_type="team",
        entity_id=entity_id,
    )

    payload = NotificationRead.model_validate(row).model_dump(by_alias=True)

    assert payload["entityType"] == "team"
    assert payload["entityId"] == entity_id
    assert payload["ticketId"] is None
