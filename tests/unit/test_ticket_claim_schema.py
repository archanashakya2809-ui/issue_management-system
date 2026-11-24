from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.claim import TicketClaimRead, TicketClaimStateRead, TicketReleaseRequest


def test_release_request_trims_valid_reason():
    payload = TicketReleaseRequest(reason="  Need specialist access  ")
    assert payload.reason == "Need specialist access"


def test_release_request_rejects_short_or_blank_reason():
    with pytest.raises(ValidationError):
        TicketReleaseRequest(reason=" no ")
    with pytest.raises(ValidationError):
        TicketReleaseRequest(reason="     ")


def test_claim_state_represents_unclaimed_ticket_available_to_current_user():
    state = TicketClaimStateRead(
        ticket_id=uuid4(),
        current_resolver_id=None,
        current_claim_number=None,
        claimed_at=None,
        is_claimed=False,
        claim_available=True,
        can_current_user_claim=True,
        can_current_user_release=False,
        has_current_user_released=False,
        claim_count=0,
        remaining_claimants_count=3,
    )
    assert state.can_current_user_claim is True
    assert state.current_resolver_id is None


def test_claim_read_contains_server_generated_claim_identity():
    claim = TicketClaimRead(
        id=uuid4(),
        ticket_id=uuid4(),
        company_id=uuid4(),
        claimed_by=uuid4(),
        claim_number=1,
        claimed_at=datetime.now(timezone.utc),
    )
    assert claim.claim_number == 1
