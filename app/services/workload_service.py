from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.workload import TeamWorkloadItem


def get_workload(
    db: Session,
    *,
    company_id: UUID,
    department_id: UUID | None = None,
) -> list[TeamWorkloadItem]:
    """Return tenant-scoped team workload using membership and active claims.

    Team roles are read from user_role_assignments, not team_members. Active
    ticket ownership is read from ticket_claims, not legacy ticket_assignments.
    """
    params: dict = {"company_id": company_id}
    department_filter = ""
    if department_id is not None:
        department_filter = "AND team.department_id = :department_id"
        params["department_id"] = department_id

    rows = db.execute(
        text(
            f"""
            WITH active_team_leads AS (
                SELECT DISTINCT ON (ura.company_id, ura.scope_team_id)
                    ura.company_id,
                    ura.scope_team_id AS team_id,
                    concat_ws(' ', u.first_name, u.last_name) AS lead_name
                FROM public.user_role_assignments AS ura
                JOIN public.roles AS assigned_role
                  ON assigned_role.id = ura.role_id
                 AND assigned_role.code = 'TEAM_LEAD'
                 AND assigned_role.is_active = TRUE
                JOIN public.team_members AS membership
                  ON membership.company_id = ura.company_id
                 AND membership.team_id = ura.scope_team_id
                 AND membership.user_id = ura.user_id
                 AND membership.left_at IS NULL
                JOIN public.users AS u
                  ON u.company_id = ura.company_id
                 AND u.id = ura.user_id
                 AND u.status = 'active'
                 AND u.deleted_at IS NULL
                WHERE ura.company_id = :company_id
                  AND ura.scope_type = 'team'
                  AND ura.scope_team_id IS NOT NULL
                  AND ura.revoked_at IS NULL
                ORDER BY
                    ura.company_id,
                    ura.scope_team_id,
                    ura.assigned_at DESC NULLS LAST,
                    ura.id
            ),
            active_claimed_tickets_by_team AS (
                SELECT DISTINCT
                    ticket.company_id,
                    ticket.id AS ticket_id,
                    team.id AS team_id
                FROM public.ticket_claims AS claim
                JOIN public.tickets AS ticket
                  ON ticket.company_id = claim.company_id
                 AND ticket.id = claim.ticket_id
                JOIN public.ticket_department_queue AS queue
                  ON queue.company_id = ticket.company_id
                 AND queue.ticket_id = ticket.id
                 AND queue.queue_status = 'CLAIMED'
                JOIN public.ticket_statuses AS ticket_status
                  ON ticket_status.company_id = ticket.company_id
                 AND ticket_status.id = ticket.status_id
                 AND ticket_status.is_terminal = FALSE
                JOIN public.teams AS team
                  ON team.company_id = ticket.company_id
                 AND team.department_id = ticket.destination_department_id
                 AND team.status = 'active'
                JOIN public.user_role_assignments AS ura
                  ON ura.company_id = claim.company_id
                 AND ura.user_id = claim.claimed_by
                 AND ura.scope_type = 'team'
                 AND ura.scope_team_id = team.id
                 AND ura.revoked_at IS NULL
                JOIN public.roles AS assigned_role
                  ON assigned_role.id = ura.role_id
                 AND assigned_role.code = 'SUPPORT_AGENT'
                 AND assigned_role.is_active = TRUE
                JOIN public.team_members AS membership
                  ON membership.company_id = ura.company_id
                 AND membership.team_id = ura.scope_team_id
                 AND membership.user_id = ura.user_id
                 AND membership.left_at IS NULL
                JOIN public.users AS agent
                  ON agent.company_id = claim.company_id
                 AND agent.id = claim.claimed_by
                 AND agent.status = 'active'
                 AND agent.deleted_at IS NULL
                WHERE claim.company_id = :company_id
                  AND claim.released_at IS NULL
                  AND public.app_is_eligible_department_agent(
                        ticket.id,
                        claim.claimed_by
                      )
            )
            SELECT
                team.id AS team_id,
                team.name AS team_name,
                team.department_id,
                department.name AS department_name,
                lead.lead_name,
                COUNT(DISTINCT member.user_id)
                    FILTER (WHERE member.left_at IS NULL)
                    AS member_count,
                COUNT(DISTINCT active_ticket.ticket_id)
                    AS active_ticket_count,
                COUNT(DISTINCT active_ticket.ticket_id)
                    FILTER (
                        WHERE sla.resolution_due_at IS NOT NULL
                          AND sla.resolution_due_at < now()
                    ) AS sla_breached_count
            FROM public.teams AS team
            JOIN public.departments AS department
              ON department.company_id = team.company_id
             AND department.id = team.department_id
            LEFT JOIN public.team_members AS member
              ON member.company_id = team.company_id
             AND member.team_id = team.id
            LEFT JOIN active_team_leads AS lead
              ON lead.company_id = team.company_id
             AND lead.team_id = team.id
            LEFT JOIN active_claimed_tickets_by_team AS active_ticket
              ON active_ticket.company_id = team.company_id
             AND active_ticket.team_id = team.id
            LEFT JOIN public.ticket_sla AS sla
              ON sla.ticket_id = active_ticket.ticket_id
            WHERE team.company_id = :company_id
              AND team.status = 'active'
              {department_filter}
            GROUP BY
                team.id,
                team.name,
                team.department_id,
                department.name,
                lead.lead_name
            ORDER BY department.name, team.name
            """
        ),
        params,
    ).mappings().all()

    return [
        TeamWorkloadItem(
            team_id=row["team_id"],
            team_name=row["team_name"],
            department_id=row["department_id"],
            department_name=row["department_name"],
            lead_name=row["lead_name"],
            member_count=int(row["member_count"] or 0),
            active_ticket_count=int(row["active_ticket_count"] or 0),
            sla_breached_count=int(row["sla_breached_count"] or 0),
        )
        for row in rows
    ]
