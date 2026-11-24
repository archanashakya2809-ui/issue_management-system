from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.ticket import Ticket
from app.models.ticket_responsibility import TicketResponsibility
from app.schemas.ticket import TicketCreate, TicketUpdate
from app.services.ticket_service import _ticket_create_request_hash


def test_ticket_create_accepts_legacy_payload_without_destination():
    payload = TicketCreate(
        domain_id=uuid4(),
        category_id=uuid4(),
        priority_id=uuid4(),
        subject="Existing frontend request",
    )

    assert payload.destination_department_id is None


def test_ticket_create_accepts_explicit_destination_department():
    department_id = uuid4()
    payload = TicketCreate(
        destination_department_id=department_id,
        domain_id=uuid4(),
        category_id=uuid4(),
        priority_id=uuid4(),
        subject="Cross-department request",
    )

    assert payload.destination_department_id == department_id


def test_ticket_create_rejects_client_supplied_assignment_targets():
    with pytest.raises(ValidationError):
        TicketCreate(
            destination_department_id=uuid4(),
            domain_id=uuid4(),
            category_id=uuid4(),
            priority_id=uuid4(),
            subject="Do not trust client assignment IDs",
            support_agent_id=uuid4(),
            team_lead_id=uuid4(),
            department_manager_id=uuid4(),
        )


def test_ticket_update_does_not_allow_changing_destination_or_responsibilities():
    with pytest.raises(ValidationError):
        TicketUpdate(
            expected_version=1,
            destination_department_id=uuid4(),
        )


def test_ticket_create_idempotency_hash_is_stable_and_destination_sensitive():
    payload = TicketCreate(
        destination_department_id=uuid4(),
        domain_id=uuid4(),
        category_id=uuid4(),
        priority_id=uuid4(),
        subject="Duplicate submission protection",
        description="One request must create one ticket.",
    )

    destination = payload.destination_department_id
    first = _ticket_create_request_hash(
        payload, destination_department_id=destination
    )
    second = _ticket_create_request_hash(
        payload, destination_department_id=destination
    )
    third = _ticket_create_request_hash(
        payload, destination_department_id=uuid4()
    )

    assert first == second
    assert first != third


def test_ticket_orm_maps_destination_department():
    assert "destination_department_id" in Ticket.__table__.columns


def test_responsibility_orm_maps_history_table():
    assert TicketResponsibility.__tablename__ == "ticket_responsibilities"
    assert "responsibility_type" in TicketResponsibility.__table__.columns
