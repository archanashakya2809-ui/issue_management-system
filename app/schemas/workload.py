from uuid import UUID

from pydantic import BaseModel


class TeamWorkloadItem(BaseModel):
    team_id: UUID
    team_name: str
    department_id: UUID
    department_name: str | None
    lead_name: str | None
    member_count: int
    active_ticket_count: int
    sla_breached_count: int
