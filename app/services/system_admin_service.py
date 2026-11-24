import time
import uuid as _uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.system_admin import (
    AuditLogItem,
    AuditLogListResponse,
    PlatformAdminItem,
    ServiceHealthItem,
    SystemHealthResponse,
    WorkflowStatusItem,
    WorkflowTransitionItem,
)

# Security-relevant action prefixes — filter via LIKE patterns
_SECURITY_ACTION_PREFIXES = ["auth.%", "user.role.%", "permission.%", "session.%", "security.%"]


def _derive_outcome(action: str) -> str:
    lower = action.lower()
    if lower.endswith(".failed") or ".fail" in lower or "locked" in lower:
        return "failed"
    return "success"


# ── Workflow Statuses ─────────────────────────────────────────────────────────
# SYSTEM_ADMIN is in the platform company (SYS) which has no ticket_statuses.
# We intentionally omit the company_id filter — RLS enforces that the caller
# can only see rows whose company passes app_has_permission_in_company().
# For SYSTEM_ADMIN with system scope this includes all tenant companies.

_INITIAL_CODES = {"DRAFT", "OPEN"}


def list_workflow_statuses(db: Session) -> list[WorkflowStatusItem]:
    rows = db.execute(
        text("""
            SELECT
                ts.id,
                ts.code,
                ts.name,
                ts.is_terminal,
                ts.is_active,
                c.name AS company_name
            FROM public.ticket_statuses ts
            JOIN public.companies c ON c.id = ts.company_id
            WHERE c.is_platform = FALSE
            ORDER BY c.name, ts.display_order ASC NULLS LAST, ts.name
        """),
    ).mappings().all()

    return [
        WorkflowStatusItem(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            is_terminal=bool(row["is_terminal"]),
            is_active=bool(row["is_active"]),
            is_initial=row["code"].upper() in _INITIAL_CODES,
            company_name=row["company_name"],
        )
        for row in rows
    ]


def update_workflow_status(
    db: Session,
    *,
    status_id: UUID,
    payload_name: str | None,
    payload_is_active: bool | None,
) -> WorkflowStatusItem:
    updates: dict = {}
    if payload_name is not None:
        updates["name"] = payload_name.strip()
    if payload_is_active is not None:
        updates["is_active"] = payload_is_active
    if not updates:
        raise HTTPException(422, "Provide at least one field to update.")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    try:
        db.execute(
            text(f"UPDATE public.ticket_statuses SET {set_clause} WHERE id = :id"),
            {**updates, "id": status_id},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to update workflow status.")

    row = db.execute(
        text("""
            SELECT ts.id, ts.code, ts.name, ts.is_terminal, ts.is_active,
                   c.name AS company_name
            FROM public.ticket_statuses ts
            JOIN public.companies c ON c.id = ts.company_id
            WHERE ts.id = :id
        """),
        {"id": status_id},
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Workflow status not found.")
    return WorkflowStatusItem(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        is_terminal=bool(row["is_terminal"]),
        is_active=bool(row["is_active"]),
        is_initial=row["code"].upper() in _INITIAL_CODES,
        company_name=row["company_name"],
    )


# ── Workflow Transitions ──────────────────────────────────────────────────────
# workflow_transitions.required_permission_id is a FK → permissions.id
# workflow_transitions.role_required_id is a FK → roles.id
# No company_id filter — same RLS reasoning as ticket_statuses above.

def list_workflow_transitions(db: Session) -> list[WorkflowTransitionItem]:
    rows = db.execute(
        text("""
            SELECT
                wt.id,
                wt.company_id,
                c.name                  AS company_name,
                wt.transition_code,
                wt.from_status_id,
                ts_from.code            AS from_status_code,
                wt.to_status_id,
                ts_to.code              AS to_status_code,
                p.code                  AS required_permission,
                r.code                  AS required_role_code,
                wt.is_active
            FROM public.workflow_transitions wt
            JOIN public.companies c
                ON c.id = wt.company_id AND c.is_platform = FALSE
            JOIN public.ticket_statuses ts_from ON ts_from.id = wt.from_status_id
            JOIN public.ticket_statuses ts_to   ON ts_to.id   = wt.to_status_id
            LEFT JOIN public.permissions p ON p.id = wt.required_permission_id
            LEFT JOIN public.roles r       ON r.id = wt.role_required_id
            ORDER BY c.name, ts_from.display_order, ts_to.display_order
        """),
    ).mappings().all()

    return [
        WorkflowTransitionItem(
            id=row["id"],
            transition_code=row["transition_code"],
            from_status_id=row["from_status_id"],
            from_status_code=row["from_status_code"],
            to_status_id=row["to_status_id"],
            to_status_code=row["to_status_code"],
            required_permission=row["required_permission"],
            required_role_code=row["required_role_code"],
            is_active=bool(row["is_active"]),
            company_name=row["company_name"],
        )
        for row in rows
    ]


def create_workflow_transition(
    db: Session,
    *,
    company_id: UUID,
    from_status_id: UUID,
    to_status_id: UUID,
    required_permission: str | None,
    required_role_code: str | None,
) -> WorkflowTransitionItem:
    permission_id: UUID | None = None
    role_id: UUID | None = None

    if required_permission:
        row = db.execute(
            text("SELECT id FROM public.permissions WHERE code = :code LIMIT 1"),
            {"code": required_permission},
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(422, f"Unknown permission code: {required_permission}")
        permission_id = row["id"]

    if required_role_code:
        row = db.execute(
            text("SELECT id FROM public.roles WHERE code = :code LIMIT 1"),
            {"code": required_role_code},
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(422, f"Unknown role code: {required_role_code}")
        role_id = row["id"]

    new_id = _uuid.uuid4()
    try:
        db.execute(
            text("""
                INSERT INTO public.workflow_transitions
                    (id, company_id, from_status_id, to_status_id,
                     required_permission_id, role_required_id, is_active)
                VALUES
                    (:id, :company_id, :from_status_id, :to_status_id,
                     :perm_id, :role_id, TRUE)
            """),
            {
                "id": new_id,
                "company_id": company_id,
                "from_status_id": from_status_id,
                "to_status_id": to_status_id,
                "perm_id": permission_id,
                "role_id": role_id,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to create workflow transition.")

    row = db.execute(
        text("""
            SELECT wt.id, wt.company_id, c.name AS company_name, wt.transition_code,
                   wt.from_status_id, ts_from.code AS from_status_code,
                   wt.to_status_id,   ts_to.code   AS to_status_code,
                   p.code AS required_permission, r.code AS required_role_code,
                   wt.is_active
            FROM public.workflow_transitions wt
            JOIN public.companies c ON c.id = wt.company_id
            JOIN public.ticket_statuses ts_from ON ts_from.id = wt.from_status_id
            JOIN public.ticket_statuses ts_to   ON ts_to.id   = wt.to_status_id
            LEFT JOIN public.permissions p ON p.id = wt.required_permission_id
            LEFT JOIN public.roles r       ON r.id = wt.role_required_id
            WHERE wt.id = :id
        """),
        {"id": new_id},
    ).mappings().one()
    return WorkflowTransitionItem(
        id=row["id"],
        transition_code=row["transition_code"],
        from_status_id=row["from_status_id"],
        from_status_code=row["from_status_code"],
        to_status_id=row["to_status_id"],
        to_status_code=row["to_status_code"],
        required_permission=row["required_permission"],
        required_role_code=row["required_role_code"],
        is_active=bool(row["is_active"]),
        company_name=row["company_name"],
    )


def update_workflow_transition(
    db: Session,
    *,
    transition_id: UUID,
    is_active: bool | None,
    required_permission: str | None,
) -> WorkflowTransitionItem:
    updates: dict = {}
    if is_active is not None:
        updates["is_active"] = is_active
    if required_permission is not None:
        row = db.execute(
            text("SELECT id FROM public.permissions WHERE code = :code LIMIT 1"),
            {"code": required_permission},
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(422, f"Unknown permission code: {required_permission}")
        updates["required_permission_id"] = row["id"]
    if not updates:
        raise HTTPException(422, "Provide at least one field to update.")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    try:
        db.execute(
            text(f"UPDATE public.workflow_transitions SET {set_clause} WHERE id = :id"),
            {**updates, "id": transition_id},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to update workflow transition.")

    row = db.execute(
        text("""
            SELECT wt.id, wt.company_id, c.name AS company_name, wt.transition_code,
                   wt.from_status_id, ts_from.code AS from_status_code,
                   wt.to_status_id,   ts_to.code   AS to_status_code,
                   p.code AS required_permission, r.code AS required_role_code,
                   wt.is_active
            FROM public.workflow_transitions wt
            JOIN public.companies c ON c.id = wt.company_id
            JOIN public.ticket_statuses ts_from ON ts_from.id = wt.from_status_id
            JOIN public.ticket_statuses ts_to   ON ts_to.id   = wt.to_status_id
            LEFT JOIN public.permissions p ON p.id = wt.required_permission_id
            LEFT JOIN public.roles r       ON r.id = wt.role_required_id
            WHERE wt.id = :id
        """),
        {"id": transition_id},
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Workflow transition not found.")
    return WorkflowTransitionItem(
        id=row["id"],
        transition_code=row["transition_code"],
        from_status_id=row["from_status_id"],
        from_status_code=row["from_status_code"],
        to_status_id=row["to_status_id"],
        to_status_code=row["to_status_code"],
        required_permission=row["required_permission"],
        required_role_code=row["required_role_code"],
        is_active=bool(row["is_active"]),
        company_name=row["company_name"],
    )


# ── Audit Logs ────────────────────────────────────────────────────────────────
# audit_logs.user_id (not actor_user_id) — confirmed from information_schema.
# No company_id filter on platform audit: RLS handles cross-company access
# via app_can_view_audit_company() which grants SYSTEM_ADMIN full visibility.

def _build_audit_items(rows) -> list[AuditLogItem]:
    items = []
    for row in rows:
        raw_name = (row["actor_name"] or "").strip()
        action = row["action"] or ""
        items.append(
            AuditLogItem(
                id=row["id"],
                company_id=row["company_id"],
                company_name=row["company_name"],
                actor_user_id=row["user_id"],
                actor_name=raw_name or None,
                action=action,
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                ip_address=str(row["ip_address"]) if row["ip_address"] else None,
                outcome=_derive_outcome(action),
                metadata=row["metadata"],
                created_at=row["created_at"],
            )
        )
    return items


_AUDIT_SELECT = """
    SELECT
        al.id,
        al.company_id,
        c.name  AS company_name,
        al.user_id,
        trim(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, '')) AS actor_name,
        al.action,
        al.entity_type,
        al.entity_id,
        al.ip_address,
        al.metadata,
        al.created_at
    FROM public.audit_logs al
    LEFT JOIN public.users    u ON u.id = al.user_id
    LEFT JOIN public.companies c ON c.id = al.company_id
"""


def list_audit_logs(
    db: Session,
    *,
    company_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AuditLogListResponse:
    params: dict = {"limit": limit, "offset": offset}
    where_parts = []

    if company_id is not None:
        where_parts.append("al.company_id = :company_id")
        params["company_id"] = company_id

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    rows = db.execute(
        text(f"{_AUDIT_SELECT} {where_clause} ORDER BY al.created_at DESC LIMIT :limit OFFSET :offset"),
        params,
    ).mappings().all()

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total = db.execute(
        text(f"SELECT COUNT(*) FROM public.audit_logs al {where_clause}"),
        count_params,
    ).scalar_one()

    return AuditLogListResponse(items=_build_audit_items(rows), total=int(total))


def list_security_events(
    db: Session,
    *,
    limit: int = 50,
    offset: int = 0,
) -> AuditLogListResponse:
    # Use prefix matching so future action subtypes are automatically included
    conditions = " OR ".join(
        f"al.action LIKE :p{i}" for i in range(len(_SECURITY_ACTION_PREFIXES))
    )
    params: dict = {
        f"p{i}": prefix for i, prefix in enumerate(_SECURITY_ACTION_PREFIXES)
    }
    params["limit"] = limit
    params["offset"] = offset

    rows = db.execute(
        text(
            f"{_AUDIT_SELECT} WHERE {conditions} "
            "ORDER BY al.created_at DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    ).mappings().all()

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total = db.execute(
        text(f"SELECT COUNT(*) FROM public.audit_logs al WHERE {conditions}"),
        count_params,
    ).scalar_one()

    return AuditLogListResponse(items=_build_audit_items(rows), total=int(total))


# ── Platform Administrators ───────────────────────────────────────────────────

def list_platform_admins(db: Session) -> list[PlatformAdminItem]:
    rows = db.execute(
        text("""
            SELECT
                u.id,
                trim(u.first_name || ' ' || COALESCE(u.last_name, '')) AS name,
                u.email,
                u.status,
                r.name  AS role_name,
                (
                    SELECT MAX(us.issued_at)
                    FROM public.user_sessions us
                    WHERE us.user_id = u.id
                ) AS last_login
            FROM public.users u
            JOIN public.user_role_assignments ura
                ON ura.user_id = u.id AND ura.revoked_at IS NULL
            JOIN public.roles r
                ON r.id = ura.role_id AND r.code = 'SYSTEM_ADMIN'
            JOIN public.companies c
                ON c.id = u.company_id AND c.is_platform = TRUE
            WHERE u.deleted_at IS NULL
            ORDER BY u.first_name, u.last_name
        """),
    ).mappings().all()

    return [
        PlatformAdminItem(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            status=row["status"],
            role_name=row["role_name"],
            last_login=row["last_login"],
        )
        for row in rows
    ]


# ── System Health ─────────────────────────────────────────────────────────────

def get_system_health(db: Session) -> SystemHealthResponse:
    db_state = "healthy"
    db_latency: float | None = None
    db_detail: str | None = None
    try:
        t0 = time.perf_counter()
        db.execute(text("SELECT 1"))
        db_latency = round((time.perf_counter() - t0) * 1000, 1)
    except Exception:
        db_state = "unhealthy"
        db_detail = "Database connectivity check failed."

    return SystemHealthResponse(
        services=[
            ServiceHealthItem(
                service="API",
                state="healthy",
                latency_ms=None,
                detail=None,
            ),
            ServiceHealthItem(
                service="PostgreSQL",
                state=db_state,
                latency_ms=db_latency,
                detail=db_detail,
            ),
        ],
        checked_at=datetime.now(timezone.utc).isoformat(),
    )
