from sqlalchemy import ForeignKeyConstraint, Index
from sqlalchemy.orm import configure_mappers

import app.models  # noqa: F401 - registers every mapped class on Base.metadata
from app.models.department import Department
from app.models.team import Team
from app.models.ticket import Ticket
from app.models.ticket_assignment import TicketAssignment
from app.models.ticket_claim import TicketClaim
from app.models.ticket_responsibility import TicketResponsibility


def _relationship_names(model) -> set[str]:
    return set(model.__mapper__.relationships.keys())


def _foreign_key_names(model) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def _index_names(model) -> set[str]:
    return {index.name for index in model.__table__.indexes if isinstance(index, Index)}


def test_all_ticket_lifecycle_mappers_configure():
    configure_mappers()

    assert {
        "assignments",
        "responsibilities",
        "claims",
        "requester",
        "requester_department",
        "destination_department",
    }.issubset(_relationship_names(Ticket))


def test_ticket_assignment_is_linked_to_ticket_team_agent_and_actor():
    configure_mappers()

    assert {"ticket", "assigned_team", "assigned_user", "assigning_user"}.issubset(
        _relationship_names(TicketAssignment)
    )
    assert _foreign_key_names(TicketAssignment) == {
        "fk_assignments_ticket_company",
        "fk_assignments_team_company",
        "fk_assignments_user_company",
        "fk_assignments_actor_company",
    }
    assert "uq_one_active_ticket_assignment" in _index_names(TicketAssignment)


def test_responsibilities_only_model_team_lead_and_department_manager_history():
    configure_mappers()

    assert {"ticket", "user", "assigning_user"}.issubset(
        _relationship_names(TicketResponsibility)
    )
    assert _foreign_key_names(TicketResponsibility) == {
        "fk_ticket_responsibilities_ticket_company",
        "fk_ticket_responsibilities_user_company",
        "fk_ticket_responsibilities_actor_company",
    }
    responsibility_check = next(
        constraint.sqltext
        for constraint in TicketResponsibility.__table__.constraints
        if getattr(constraint, "name", None) == "chk_ticket_responsibility_type"
    )
    assert "TEAM_LEAD" in str(responsibility_check)
    assert "DEPARTMENT_MANAGER" in str(responsibility_check)
    assert "SUPPORT_AGENT" not in str(responsibility_check)


def test_claim_history_is_linked_to_ticket_and_claimant():
    configure_mappers()

    assert {"ticket", "claimant"}.issubset(_relationship_names(TicketClaim))
    assert _foreign_key_names(TicketClaim) == {
        "fk_ticket_claims_ticket_company",
        "fk_ticket_claims_user_company",
    }
    assert "uq_ticket_claims_one_active_per_ticket" in _index_names(TicketClaim)


def test_department_and_team_mappings_match_company_scoped_relationships():
    configure_mappers()

    assert "uq_departments_company_id_id" in {
        constraint.name for constraint in Department.__table__.constraints
    }
    assert "uq_teams_company_id_id" in {
        constraint.name for constraint in Team.__table__.constraints
    }
    assert "fk_teams_department_company" in _foreign_key_names(Team)


def test_routing_read_schema_exposes_the_three_lifecycle_tracks():
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.schemas.ticket_routing import TicketRoutingRead

    ticket_id = uuid4()
    team_id = uuid4()
    agent_id = uuid4()
    lead_id = uuid4()
    manager_id = uuid4()
    now = datetime.now(timezone.utc)
    payload = TicketRoutingRead(
        ticket_id=ticket_id,
        destination_department_id=uuid4(),
        active_assignment={
            "id": uuid4(),
            "ticket_id": ticket_id,
            "assigned_team_id": team_id,
            "assigned_user_id": agent_id,
            "assigned_by": uuid4(),
            "assigned_at": now,
            "unassigned_at": None,
            "reason": "Automatic assignment",
        },
        active_responsibilities=[
            {
                "id": uuid4(),
                "ticket_id": ticket_id,
                "responsibility_type": "TEAM_LEAD",
                "user_id": lead_id,
                "assigned_by": uuid4(),
                "assigned_at": now,
                "unassigned_at": None,
                "reason": "Team lead snapshot",
            },
            {
                "id": uuid4(),
                "ticket_id": ticket_id,
                "responsibility_type": "DEPARTMENT_MANAGER",
                "user_id": manager_id,
                "assigned_by": uuid4(),
                "assigned_at": now,
                "unassigned_at": None,
                "reason": "Department manager snapshot",
            },
        ],
        current_claim=None,
        assignment_history=[],
        responsibility_history=[],
        claim_history=[],
    )

    assert payload.active_assignment.assigned_user_id == agent_id
    assert {row.responsibility_type for row in payload.active_responsibilities} == {
        "TEAM_LEAD",
        "DEPARTMENT_MANAGER",
    }
    assert payload.current_claim is None
