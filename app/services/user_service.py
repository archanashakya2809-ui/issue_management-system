from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.user import User
from app.schemas.system_admin import (
    RoleItem,
    SysUserCreate,
    SysUserListItem,
    SysUserListResponse,
    SysUserUpdate,
    SysUserRestore,
)
from app.schemas.user import ProfileUpdate, UserListItem, UserListResponse
from app.services.audit_service import write_audit_event

# Role code → scope_type mapping (mirrors auth_service._ROLE_SCOPE)
_ROLE_SCOPE: dict[str, str] = {
    "employee": "self",
    "support_agent": "team",
    "team_lead": "team",
    "department_manager": "department",
    "company_admin": "company",
    "system_admin": "system",
}


def _compute_scope(
    role_code: str,
    dept_id: UUID | None,
    team_id: UUID | None,
) -> tuple[str, UUID | None, UUID | None]:
    """Return (scope_type, scope_department_id, scope_team_id) satisfying DB constraint."""
    natural = _ROLE_SCOPE.get(role_code.lower(), "self")
    if natural == "system":
        return "system", None, None
    if natural == "company":
        return "company", None, None
    if natural == "department":
        return ("department", dept_id, None) if dept_id else ("company", None, None)
    if natural == "team":
        return ("team", None, team_id) if team_id else ("company", None, None)
    return "self", None, None  # employee


def _team_membership_type(role_code: str) -> str:
    """Return the organizational team membership classification for a role."""
    return "lead" if role_code.lower() == "team_lead" else "member"


def _validate_role_org_requirements(
    role_code: str, *, department_id: UUID | None, team_id: UUID | None
) -> None:
    normalized = role_code.lower()
    if normalized in {"support_agent", "team_lead"} and (team_id is None or department_id is None):
        raise HTTPException(422, detail=f"{role_code} requires an assigned department and active team.")
    if normalized == "department_manager" and department_id is None:
        raise HTTPException(422, detail="Department Manager requires an assigned department.")


# Base FROM clause shared by list and count queries
_USER_FROM = """
    FROM public.users u
    JOIN public.companies c ON c.id = u.company_id AND c.is_platform = FALSE
    LEFT JOIN LATERAL (
        SELECT tm.team_id
        FROM public.team_members tm
        WHERE tm.user_id = u.id AND tm.left_at IS NULL
        ORDER BY tm.joined_at DESC LIMIT 1
    ) tm_sub ON TRUE
    LEFT JOIN LATERAL (
        SELECT lower(r2.code) AS role_code
        FROM public.user_role_assignments ura2
        JOIN public.roles r2 ON r2.id = ura2.role_id
        WHERE ura2.user_id = u.id AND ura2.revoked_at IS NULL AND r2.is_active = TRUE
        ORDER BY r2.code LIMIT 1
    ) r_sub ON TRUE
"""


def list_users(
    db: Session,
    *,
    company_id: UUID,
    department_id: UUID | None = None,
    team_id: UUID | None = None,
    q: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> UserListResponse:
    """Return users visible to the caller via RLS, with an optional primary role."""
    filters = ["u.company_id = :company_id", "u.deleted_at IS NULL", "u.status = 'active'"]
    params: dict = {"company_id": company_id, "limit": limit, "offset": offset}

    if department_id is not None:
        filters.append("u.department_id = :department_id")
        params["department_id"] = department_id

    if team_id is not None:
        filters.append(
            "EXISTS ("
            "  SELECT 1 FROM public.team_members tm"
            "  WHERE tm.user_id = u.id AND tm.team_id = :team_id AND tm.left_at IS NULL"
            ")"
        )
        params["team_id"] = team_id

    if q:
        filters.append(
            "(lower(u.first_name || ' ' || COALESCE(u.last_name, '')) LIKE :q"
            " OR lower(u.email) LIKE :q)"
        )
        params["q"] = f"%{q.lower()}%"

    where = " AND ".join(filters)

    rows = db.execute(
        text(f"""
            SELECT
                u.id,
                u.company_id,
                u.employee_code,
                u.first_name,
                u.last_name,
                u.email,
                u.department_id,
                u.status,
                (
                    SELECT lower(r.code)
                    FROM public.user_role_assignments ura
                    JOIN public.roles r ON r.id = ura.role_id
                    WHERE ura.user_id = u.id
                      AND ura.company_id = u.company_id
                      AND ura.revoked_at IS NULL
                      AND r.is_active = TRUE
                    ORDER BY r.code
                    LIMIT 1
                ) AS primary_role
            FROM public.users u
            WHERE {where}
            ORDER BY u.first_name, u.last_name
            LIMIT :limit OFFSET :offset
        """),
        params,
    ).mappings().all()

    total_row = db.execute(
        text(f"SELECT COUNT(*) FROM public.users u WHERE {where}"),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    ).scalar_one()

    items = [
        UserListItem(
            id=row["id"],
            company_id=row["company_id"],
            employee_code=row["employee_code"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            email=row["email"],
            department_id=row["department_id"],
            status=row["status"],
            primary_role=row["primary_role"],
        )
        for row in rows
    ]

    return UserListResponse(items=items, total=int(total_row), limit=limit, offset=offset)


def update_user_profile(
    db: Session,
    *,
    current_user: User,
    payload: ProfileUpdate,
) -> User:
    """Update the caller's own first/last name."""
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Provide at least one field to update.")

    actual = {field: value for field, value in changes.items() if getattr(current_user, field) != value}
    if not actual:
        raise HTTPException(status_code=422, detail="The supplied values are identical to the current values.")

    set_clause = ", ".join(f"{field} = :{field}" for field in actual)
    actual["user_id"] = current_user.id
    actual["company_id"] = current_user.company_id

    db.execute(
        text(f"UPDATE public.users SET {set_clause}, updated_at = now() WHERE id = :user_id AND company_id = :company_id"),
        actual,
    )

    write_audit_event(
        db,
        company_id=current_user.company_id,
        action="user.profile.update",
        entity_type="user",
        entity_id=current_user.id,
        metadata={"changed_fields": sorted(k for k in actual if k not in ("user_id", "company_id"))},
    )

    db.commit()
    db.refresh(current_user)
    return current_user


# ── System-admin cross-tenant user management ─────────────────────────────────

def sys_list_users(
    db: Session,
    *,
    company_id: UUID | None = None,
    department_id: UUID | None = None,
    team_id: UUID | None = None,
    q: str | None = None,
    status: str | None = None,
    role_code: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> SysUserListResponse:
    filters = ["u.deleted_at IS NULL"]
    params: dict = {}

    if company_id:
        filters.append("u.company_id = :company_id")
        params["company_id"] = company_id
    if department_id:
        filters.append("u.department_id = :department_id")
        params["department_id"] = department_id
    if team_id:
        filters.append("tm_sub.team_id = :team_id")
        params["team_id"] = team_id
    if status:
        filters.append("u.status = :status")
        params["status"] = status
    if role_code:
        filters.append("r_sub.role_code = :role_code_filter")
        params["role_code_filter"] = role_code.lower()
    if q:
        filters.append(
            "(lower(u.first_name || ' ' || COALESCE(u.last_name,'')) LIKE :q"
            " OR lower(u.email) LIKE :q"
            " OR lower(COALESCE(u.employee_code,'')) LIKE :q)"
        )
        params["q"] = f"%{q.lower()}%"

    where = "WHERE " + " AND ".join(filters)

    total = db.execute(text(f"SELECT COUNT(*) {_USER_FROM} {where}"), params).scalar_one()

    rows = db.execute(
        text(f"""
            SELECT u.id, u.company_id, c.name AS company_name,
                   u.employee_code, u.first_name, u.last_name, u.email,
                   u.department_id, d.name AS department_name,
                   tm_sub.team_id, t.name AS team_name,
                   r_sub.role_code AS primary_role,
                   u.status, u.last_login_at, u.created_at
            {_USER_FROM}
            LEFT JOIN public.departments d ON d.id = u.department_id
            LEFT JOIN public.teams t ON t.id = tm_sub.team_id
            {where}
            ORDER BY u.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()

    return SysUserListResponse(
        items=[
            SysUserListItem(
                id=r["id"],
                company_id=r["company_id"],
                company_name=r["company_name"],
                employee_code=r["employee_code"],
                first_name=r["first_name"],
                last_name=r["last_name"],
                email=r["email"],
                department_id=r["department_id"],
                department_name=r["department_name"],
                team_id=r["team_id"],
                team_name=r["team_name"],
                primary_role=r["primary_role"],
                status=r["status"],
                last_login_at=r["last_login_at"],
                created_at=r["created_at"],
            )
            for r in rows
        ],
        total=int(total),
        limit=limit,
        offset=offset,
    )


def sys_list_deleted_users(
    db: Session,
    *,
    company_id: UUID | None = None,
    department_id: UUID | None = None,
    q: str | None = None,
    status: str | None = None,
    role_code: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> SysUserListResponse:
    """List soft-deleted tenant users, including their last historical role/team."""
    filters = ["u.deleted_at IS NOT NULL", "c.is_platform = FALSE"]
    params: dict = {}
    if company_id:
        filters.append("u.company_id = :company_id")
        params["company_id"] = company_id
    if department_id:
        filters.append("u.department_id = :department_id")
        params["department_id"] = department_id
    if status:
        filters.append("u.status = :status")
        params["status"] = status
    if role_code:
        filters.append("r_sub.role_code = :role_code_filter")
        params["role_code_filter"] = role_code.lower()
    if q:
        filters.append(
            "(lower(u.first_name || ' ' || COALESCE(u.last_name,'')) LIKE :q"
            " OR lower(u.email) LIKE :q"
            " OR lower(COALESCE(u.employee_code,'')) LIKE :q)"
        )
        params["q"] = f"%{q.lower()}%"

    where = "WHERE " + " AND ".join(filters)
    from_sql = """
        FROM public.users u
        JOIN public.companies c ON c.id = u.company_id
        LEFT JOIN public.departments d ON d.id = u.department_id AND d.company_id = u.company_id
        LEFT JOIN LATERAL (
            SELECT tm.team_id
            FROM public.team_members tm
            WHERE tm.company_id = u.company_id AND tm.user_id = u.id
            ORDER BY tm.joined_at DESC, tm.id DESC
            LIMIT 1
        ) tm_sub ON TRUE
        LEFT JOIN public.teams t ON t.id = tm_sub.team_id AND t.company_id = u.company_id
        LEFT JOIN LATERAL (
            SELECT lower(r.code) AS role_code
            FROM public.user_role_assignments ura
            JOIN public.roles r ON r.id = ura.role_id
            WHERE ura.company_id = u.company_id AND ura.user_id = u.id
            ORDER BY ura.assigned_at DESC, ura.id DESC
            LIMIT 1
        ) r_sub ON TRUE
    """

    total = db.execute(text(f"SELECT COUNT(*) {from_sql} {where}"), params).scalar_one()
    rows = db.execute(
        text(f"""
            SELECT u.id, u.company_id, c.name AS company_name,
                   u.employee_code, u.first_name, u.last_name, u.email,
                   u.department_id, d.name AS department_name,
                   tm_sub.team_id, t.name AS team_name,
                   r_sub.role_code AS primary_role,
                   u.status, u.last_login_at, u.created_at, u.deleted_at
            {from_sql}
            {where}
            ORDER BY u.deleted_at DESC, u.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": limit, "offset": offset},
    ).mappings().all()

    return SysUserListResponse(
        items=[
            SysUserListItem(
                id=r["id"], company_id=r["company_id"], company_name=r["company_name"],
                employee_code=r["employee_code"], first_name=r["first_name"],
                last_name=r["last_name"], email=r["email"],
                department_id=r["department_id"], department_name=r["department_name"],
                team_id=r["team_id"], team_name=r["team_name"],
                primary_role=r["primary_role"], status=r["status"],
                last_login_at=r["last_login_at"], created_at=r["created_at"],
                deleted_at=r["deleted_at"],
            )
            for r in rows
        ],
        total=int(total), limit=limit, offset=offset,
    )

def sys_create_user(
    db: Session,
    *,
    current_user: User,
    payload: SysUserCreate,
) -> SysUserListItem:
    # Pre-fetch joined names (within transaction — GUC still set)
    co = db.execute(
        text("SELECT name FROM public.companies WHERE id = :id AND is_platform = FALSE"),
        {"id": payload.company_id},
    ).mappings().first()
    if not co:
        raise HTTPException(status_code=400, detail="Company not found.")

    dept_name: str | None = None
    if payload.department_id:
        dr = db.execute(
            text("SELECT name FROM public.departments WHERE id = :id AND company_id = :cid"),
            {"id": payload.department_id, "cid": payload.company_id},
        ).mappings().first()
        if not dr:
            raise HTTPException(status_code=400, detail="Department not found in this company.")
        dept_name = dr["name"]

    team_name: str | None = None
    if payload.team_id:
        tr = db.execute(
            text("SELECT name, department_id, status FROM public.teams WHERE id = :id AND company_id = :cid"),
            {"id": payload.team_id, "cid": payload.company_id},
        ).mappings().first()
        if not tr or tr["status"] != "active":
            raise HTTPException(status_code=400, detail="Team not found or inactive in this company.")
        if tr["department_id"] != payload.department_id:
            raise HTTPException(status_code=422, detail="The selected team must belong to the selected department.")
        team_name = tr["name"]

    role = db.execute(
        text("SELECT id FROM public.roles WHERE lower(code) = lower(:code) AND is_active = TRUE"),
        {"code": payload.role_code},
    ).mappings().first()
    if not role:
        raise HTTPException(status_code=400, detail=f"Role '{payload.role_code}' not found.")
    _validate_role_org_requirements(
        payload.role_code, department_id=payload.department_id, team_id=payload.team_id
    )

    pw_hash = hash_password(payload.password)

    try:
        user_row = db.execute(
            text("""
                INSERT INTO public.users
                    (company_id, first_name, last_name, email, employee_code, department_id, password_hash, status)
                VALUES
                    (:company_id, :first_name, :last_name, :email, :employee_code, :department_id, :password_hash, :status)
                RETURNING id, created_at, last_login_at
            """),
            {
                "company_id": payload.company_id,
                "first_name": payload.first_name.strip(),
                "last_name": payload.last_name.strip() if payload.last_name else None,
                "email": payload.email.lower(),
                "employee_code": payload.employee_code or None,
                "department_id": payload.department_id,
                "password_hash": pw_hash,
                "status": payload.status,
            },
        ).mappings().one()

        user_id = user_row["id"]

        scope_type, scope_dept_id, scope_team_id = _compute_scope(
            payload.role_code, payload.department_id, payload.team_id
        )

        # Insert team membership BEFORE URA when role is team-scoped so the
        # validate_user_role_assignment_scope trigger can confirm membership.
        if payload.team_id:
            db.execute(
                text("""
                    INSERT INTO public.team_members (company_id, team_id, user_id, membership_type, is_primary)
                    VALUES (:cid, :tid, :uid, :membership_type, TRUE)
                """),
                {"cid": payload.company_id, "tid": payload.team_id, "uid": user_id,
                 "membership_type": _team_membership_type(payload.role_code)},
            )

        db.execute(
            text("""
                INSERT INTO public.user_role_assignments
                    (user_id, role_id, company_id, scope_type, scope_department_id, scope_team_id)
                VALUES
                    (:user_id, :role_id, :company_id, :scope_type, :sdept, :steam)
            """),
            {
                "user_id": user_id,
                "role_id": role["id"],
                "company_id": payload.company_id,
                "scope_type": scope_type,
                "sdept": scope_dept_id,
                "steam": scope_team_id,
            },
        )

        write_audit_event(
            db,
            company_id=payload.company_id,
            action="user.create",
            entity_type="user",
            entity_id=user_id,
            metadata={"created_by": str(current_user.id), "role": payload.role_code},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        err = str(exc)
        if "uq_users_company_email_ci" in err:
            raise HTTPException(status_code=409, detail="A user with this email already exists in this company.")
        if "uq_users_company_employee" in err:
            raise HTTPException(status_code=409, detail="Employee code already in use within this company.")
        if "uq_one_active_team_lead" in err:
            raise HTTPException(status_code=409, detail="This team already has an active Team Lead.")
        if "row-level security" in err or "violates row-level security policy" in err:
            raise HTTPException(status_code=403, detail="Insufficient database permissions to complete this operation.")
        raise HTTPException(status_code=500, detail="Failed to create user.") from exc

    return SysUserListItem(
        id=user_row["id"],
        company_id=payload.company_id,
        company_name=co["name"],
        employee_code=payload.employee_code or None,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip() if payload.last_name else None,
        email=payload.email.lower(),
        department_id=payload.department_id,
        department_name=dept_name,
        team_id=payload.team_id,
        team_name=team_name,
        primary_role=payload.role_code.lower(),
        status=payload.status,
        last_login_at=None,
        created_at=user_row["created_at"],
    )


def sys_update_user(
    db: Session,
    *,
    current_user: User,
    user_id: UUID,
    company_id: UUID,
    payload: SysUserUpdate,
) -> SysUserListItem:
    # Snapshot current state (within transaction)
    cur = db.execute(
        text("""
            SELECT u.first_name, u.last_name, u.email, u.employee_code,
                   u.department_id, u.status, u.last_login_at, u.created_at,
                   (
                       SELECT tm.team_id FROM public.team_members tm
                       WHERE tm.user_id = u.id AND tm.left_at IS NULL
                       ORDER BY tm.joined_at DESC LIMIT 1
                   ) AS team_id,
                   (
                       SELECT lower(r.code) FROM public.user_role_assignments ura
                       JOIN public.roles r ON r.id = ura.role_id
                       WHERE ura.user_id = u.id AND ura.company_id = :cid
                         AND ura.revoked_at IS NULL
                       LIMIT 1
                   ) AS primary_role,
                   c.name AS company_name
            FROM public.users u
            JOIN public.companies c ON c.id = u.company_id
            WHERE u.id = :uid AND u.company_id = :cid AND u.deleted_at IS NULL
        """),
        {"uid": user_id, "cid": company_id},
    ).mappings().first()
    if not cur:
        raise HTTPException(status_code=404, detail="User not found.")

    # Determine final organisational state
    fs = payload.model_fields_set
    final_dept_id = payload.department_id if "department_id" in fs else cur["department_id"]
    final_team_id = payload.team_id if "team_id" in fs else cur["team_id"]
    final_role = payload.role_code.lower() if payload.role_code else cur["primary_role"]
    _validate_role_org_requirements(
        final_role, department_id=final_dept_id, team_id=final_team_id
    )

    # Pre-fetch display names
    dept_name: str | None = None
    if final_dept_id:
        dept_name = db.execute(
            text(
                "SELECT name FROM public.departments "
                "WHERE id = :id AND company_id = :cid AND status = 'active'"
            ),
            {"id": final_dept_id, "cid": company_id},
        ).scalar_one_or_none()
        if dept_name is None:
            raise HTTPException(400, detail="Department not found or inactive in this company.")

    team_name: str | None = None
    if final_team_id:
        team_row = db.execute(
            text(
                "SELECT name, department_id, status FROM public.teams "
                "WHERE id = :id AND company_id = :cid"
            ),
            {"id": final_team_id, "cid": company_id},
        ).mappings().first()
        if team_row is None or team_row["status"] != "active":
            raise HTTPException(400, detail="Team not found or inactive in this company.")
        if team_row["department_id"] != final_dept_id:
            raise HTTPException(422, detail="The selected team must belong to the user's department.")
        team_name = team_row["name"]

    try:
        role_changed = payload.role_code is not None and payload.role_code.lower() != cur["primary_role"]
        team_changed = "team_id" in fs and final_team_id != cur["team_id"]
        department_changed = "department_id" in fs and final_dept_id != cur["department_id"]
        desired_membership_type = _team_membership_type(final_role)

        # End old memberships and revoke department-scoped assignments before
        # changing users.department_id. The database's lifecycle trigger requires
        # this ordering and also revokes old team-scoped grants on membership end.
        if team_changed or department_changed or role_changed:
            db.execute(
                text(
                    "UPDATE public.team_members SET left_at = NOW() "
                    "WHERE user_id = :uid AND company_id = :cid AND left_at IS NULL"
                ),
                {"uid": user_id, "cid": company_id},
            )
        if role_changed:
            db.execute(
                text(
                    "UPDATE public.user_role_assignments SET revoked_at = COALESCE(revoked_at, NOW()) "
                    "WHERE user_id = :uid AND company_id = :cid AND revoked_at IS NULL"
                ),
                {"uid": user_id, "cid": company_id},
            )
        elif department_changed:
            db.execute(
                text(
                    "UPDATE public.user_role_assignments SET revoked_at = COALESCE(revoked_at, NOW()) "
                    "WHERE user_id = :uid AND company_id = :cid "
                    "AND revoked_at IS NULL AND scope_type = 'department'"
                ),
                {"uid": user_id, "cid": company_id},
            )

        # Build and apply the user-field update only after stale org scopes end.
        set_parts = ["updated_at = NOW()"]
        params: dict = {"uid": user_id, "cid": company_id}
        if payload.first_name is not None:
            set_parts.append("first_name = :first_name")
            params["first_name"] = payload.first_name.strip()
        if payload.last_name is not None:
            set_parts.append("last_name = :last_name")
            params["last_name"] = payload.last_name.strip()
        if payload.employee_code is not None:
            set_parts.append("employee_code = :employee_code")
            params["employee_code"] = payload.employee_code or None
        if department_changed:
            set_parts.append("department_id = :dept_id")
            params["dept_id"] = final_dept_id
        if payload.status is not None:
            set_parts.append("status = :status")
            params["status"] = payload.status

        result = db.execute(
            text(f"""
                UPDATE public.users SET {', '.join(set_parts)}
                WHERE id = :uid AND company_id = :cid AND deleted_at IS NULL
                RETURNING first_name, last_name, email, employee_code, department_id, status, last_login_at, created_at
            """),
            params,
        ).mappings().first()
        if not result:
            raise HTTPException(status_code=404, detail="User not found.")

        # Restore the active membership using the final role classification.
        if final_team_id and (team_changed or department_changed or role_changed):
            db.execute(
                text(
                    """
                    INSERT INTO public.team_members
                        (company_id, team_id, user_id, membership_type, is_primary)
                    VALUES (:cid, :tid, :uid, :membership_type, TRUE)
                    """
                ),
                {
                    "cid": company_id,
                    "tid": final_team_id,
                    "uid": user_id,
                    "membership_type": desired_membership_type,
                },
            )
        elif final_team_id and final_role == "team_lead":
            # Repair legacy Team Lead records whose membership type was saved
            # as 'member' by previous backend versions.
            db.execute(
                text(
                    """
                    UPDATE public.team_members SET membership_type = 'lead'
                    WHERE company_id = :cid AND team_id = :tid
                      AND user_id = :uid AND left_at IS NULL
                    """
                ),
                {"cid": company_id, "tid": final_team_id, "uid": user_id},
            )

        # Role changes always replace the active grant. A department move
        # refreshes a Department Manager's scope; a team move refreshes a
        # Support Agent/Team Lead's team-scoped grant.
        needs_new_role_assignment = (
            role_changed
            or (department_changed and final_role == "department_manager")
            or (team_changed and final_role in {"support_agent", "team_lead"})
        )
        if needs_new_role_assignment:
            new_role = db.execute(
                text(
                    "SELECT id FROM public.roles "
                    "WHERE lower(code) = lower(:code) AND is_active = TRUE"
                ),
                {"code": final_role},
            ).mappings().first()
            if not new_role:
                raise HTTPException(status_code=400, detail=f"Role '{final_role}' not found.")
            scope_type, scope_dept_id, scope_team_id = _compute_scope(
                final_role, final_dept_id, final_team_id
            )
            db.execute(
                text(
                    """
                    INSERT INTO public.user_role_assignments
                        (user_id, role_id, company_id, scope_type, scope_department_id, scope_team_id)
                    VALUES (:uid, :rid, :cid, :scope_type, :sdept, :steam)
                    """
                ),
                {
                    "uid": user_id,
                    "rid": new_role["id"],
                    "cid": company_id,
                    "scope_type": scope_type,
                    "sdept": scope_dept_id,
                    "steam": scope_team_id,
                },
            )

        write_audit_event(
            db, company_id=company_id,
            action="user.update", entity_type="user", entity_id=user_id,
            metadata={"changed_by": str(current_user.id)},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        err = str(exc)
        if "uq_users_company_email_ci" in err:
            raise HTTPException(status_code=409, detail="Email already in use in this company.")
        if "uq_users_company_employee" in err:
            raise HTTPException(status_code=409, detail="Employee code already in use.")
        if "uq_one_active_team_lead" in err:
            raise HTTPException(status_code=409, detail="This team already has an active Team Lead.")
        if "row-level security" in err or "violates row-level security policy" in err:
            raise HTTPException(status_code=403, detail="Insufficient database permissions to complete this operation.")
        raise HTTPException(status_code=500, detail="Failed to update user.") from exc

    return SysUserListItem(
        id=user_id,
        company_id=company_id,
        company_name=cur["company_name"],
        employee_code=result["employee_code"],
        first_name=result["first_name"],
        last_name=result["last_name"],
        email=result["email"],
        department_id=result["department_id"],
        department_name=dept_name,
        team_id=final_team_id,
        team_name=team_name,
        primary_role=final_role,
        status=result["status"],
        last_login_at=result["last_login_at"],
        created_at=result["created_at"],
    )


def sys_change_user_status(
    db: Session,
    *,
    current_user: User,
    user_id: UUID,
    company_id: UUID,
    new_status: str,
) -> None:
    # Guard: prevent deactivating the last active system admin
    if new_status in ("inactive", "locked"):
        is_sysadmin = db.execute(
            text("""
                SELECT 1 FROM public.user_role_assignments ura
                JOIN public.roles r ON r.id = ura.role_id
                WHERE ura.user_id = :uid AND ura.revoked_at IS NULL AND lower(r.code) = 'system_admin'
            """),
            {"uid": user_id},
        ).scalar_one_or_none()
        if is_sysadmin:
            active_count = db.execute(
                text("""
                    SELECT COUNT(*) FROM public.users u
                    JOIN public.user_role_assignments ura ON ura.user_id = u.id AND ura.revoked_at IS NULL
                    JOIN public.roles r ON r.id = ura.role_id AND lower(r.code) = 'system_admin'
                    WHERE u.status = 'active' AND u.deleted_at IS NULL
                """),
            ).scalar_one()
            if int(active_count) <= 1:
                raise HTTPException(status_code=400, detail="Cannot deactivate the last active System Admin.")

    try:
        result = db.execute(
            text("""
                UPDATE public.users SET status = :status, updated_at = NOW()
                WHERE id = :uid AND company_id = :cid AND deleted_at IS NULL
                RETURNING id
            """),
            {"status": new_status, "uid": user_id, "cid": company_id},
        ).mappings().first()
        if not result:
            raise HTTPException(status_code=404, detail="User not found.")
        write_audit_event(
            db, company_id=company_id,
            action=f"user.status.{new_status}", entity_type="user", entity_id=user_id,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update user status.") from exc


def sys_delete_user(
    db: Session,
    *,
    current_user: User,
    user_id: UUID,
    company_id: UUID,
) -> None:
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")

    is_sysadmin = db.execute(
        text("""
            SELECT 1 FROM public.user_role_assignments ura
            JOIN public.roles r ON r.id = ura.role_id
            WHERE ura.user_id = :uid AND ura.revoked_at IS NULL AND lower(r.code) = 'system_admin'
        """),
        {"uid": user_id},
    ).scalar_one_or_none()
    if is_sysadmin:
        count = db.execute(
            text("""
                SELECT COUNT(*) FROM public.users u
                JOIN public.user_role_assignments ura ON ura.user_id = u.id AND ura.revoked_at IS NULL
                JOIN public.roles r ON r.id = ura.role_id AND lower(r.code) = 'system_admin'
                WHERE u.deleted_at IS NULL
            """),
        ).scalar_one()
        if int(count) <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last System Admin.")

    try:
        result = db.execute(
            text("""
                UPDATE public.users
                SET deleted_at = NOW(), status = 'inactive', updated_at = NOW()
                WHERE id = :uid AND company_id = :cid AND deleted_at IS NULL
                RETURNING id
            """),
            {"uid": user_id, "cid": company_id},
        ).mappings().first()
        if not result:
            raise HTTPException(status_code=404, detail="User not found.")

        db.execute(
            text("UPDATE public.user_role_assignments SET revoked_at = NOW() WHERE user_id = :uid AND revoked_at IS NULL"),
            {"uid": user_id},
        )
        db.execute(
            text("UPDATE public.team_members SET left_at = NOW() WHERE user_id = :uid AND left_at IS NULL"),
            {"uid": user_id},
        )
        db.execute(
            text("UPDATE public.user_sessions SET revoked_at = COALESCE(revoked_at, NOW()) WHERE user_id = :uid AND company_id = :cid AND revoked_at IS NULL"),
            {"uid": user_id, "cid": company_id},
        )
        db.execute(
            text("UPDATE public.user_domains SET revoked_at = COALESCE(revoked_at, NOW()) WHERE user_id = :uid AND company_id = :cid AND revoked_at IS NULL"),
            {"uid": user_id, "cid": company_id},
        )
        write_audit_event(
            db, company_id=company_id,
            action="user.delete", entity_type="user", entity_id=user_id,
            metadata={"deleted_by": str(current_user.id)},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete user.") from exc


def sys_restore_user(
    db: Session,
    *,
    current_user: User,
    user_id: UUID,
    company_id: UUID,
    payload: SysUserRestore,
) -> SysUserListItem:
    """Restore a soft-deleted tenant account with a new, explicit role grant.

    Historical user_role_assignments and team_members rows remain revoked/ended.
    Old sessions and individual domain grants are invalidated, not resurrected.
    """
    try:
        target = db.execute(
            text("""
                SELECT id, company_id, first_name, last_name, email, employee_code,
                       department_id, status, last_login_at, created_at
                FROM public.users
                WHERE id = :uid AND company_id = :cid AND deleted_at IS NOT NULL
                FOR UPDATE
            """),
            {"uid": user_id, "cid": company_id},
        ).mappings().first()
        if not target:
            raise HTTPException(status_code=404, detail="Deleted user not found.")
        if user_id == current_user.id:
            raise HTTPException(status_code=400, detail="You cannot restore your own account.")

        company = db.execute(
            text("SELECT name, is_platform FROM public.companies WHERE id = :cid"),
            {"cid": company_id},
        ).mappings().first()
        if not company or company["is_platform"]:
            raise HTTPException(status_code=404, detail="Tenant company not found.")

        role_code = payload.role_code.strip().lower()
        if role_code == "system_admin":
            raise HTTPException(
                status_code=422,
                detail="System Admin accounts must be managed through Platform Administrator management.",
            )
        if role_code not in {"employee", "support_agent", "team_lead", "department_manager", "company_admin"}:
            raise HTTPException(status_code=422, detail="Unsupported tenant role for user restoration.")

        role = db.execute(
            text("SELECT id, lower(code) AS code FROM public.roles WHERE lower(code) = :code AND is_active = TRUE"),
            {"code": role_code},
        ).mappings().first()
        if not role:
            raise HTTPException(status_code=422, detail="The selected role does not exist or is inactive.")

        department_id = payload.department_id
        team_id = payload.team_id
        department_name = None
        team_name = None

        if department_id:
            department = db.execute(
                text("""
                    SELECT name FROM public.departments
                    WHERE id = :did AND company_id = :cid AND status = 'active'
                """),
                {"did": department_id, "cid": company_id},
            ).mappings().first()
            if not department:
                raise HTTPException(status_code=422, detail="Select an active department in the user's company.")
            department_name = department["name"]

        if role_code in {"support_agent", "team_lead", "department_manager"} and not department_id:
            raise HTTPException(status_code=422, detail=f"{role_code} requires an active department.")
        if role_code in {"support_agent", "team_lead"} and not team_id:
            raise HTTPException(status_code=422, detail=f"{role_code} requires an active team.")
        if role_code not in {"support_agent", "team_lead"} and team_id:
            raise HTTPException(status_code=422, detail="Only Support Agents and Team Leads may be restored with a team assignment.")

        if team_id:
            team = db.execute(
                text("""
                    SELECT name, department_id FROM public.teams
                    WHERE id = :tid AND company_id = :cid AND status = 'active'
                """),
                {"tid": team_id, "cid": company_id},
            ).mappings().first()
            if not team:
                raise HTTPException(status_code=422, detail="Select an active team in the user's company.")
            if team["department_id"] != department_id:
                raise HTTPException(status_code=422, detail="The selected team must belong to the selected department.")
            team_name = team["name"]

        # A destination department must not end up with multiple active managers.
        if role_code == "department_manager":
            # Serialize concurrent restoration attempts for the same department.
            # This prevents two soft-deleted accounts being restored as managers
            # at the same time after both pass the active-manager check.
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"user-restore-department-manager:{company_id}:{department_id}"},
            )
            existing_manager = db.execute(
                text("""
                    SELECT ura.user_id
                    FROM public.user_role_assignments ura
                    JOIN public.roles r ON r.id = ura.role_id
                    JOIN public.users u ON u.id = ura.user_id AND u.company_id = ura.company_id
                    WHERE ura.company_id = :cid
                      AND ura.scope_type = 'department'
                      AND ura.scope_department_id = :did
                      AND ura.revoked_at IS NULL
                      AND lower(r.code) = 'department_manager'
                      AND r.is_active = TRUE
                      AND u.status = 'active'
                      AND u.deleted_at IS NULL
                      AND u.id <> :uid
                    LIMIT 1
                """),
                {"cid": company_id, "did": department_id, "uid": user_id},
            ).scalar_one_or_none()
            if existing_manager:
                raise HTTPException(
                    status_code=409,
                    detail="This department already has an active Department Manager. Resolve that assignment before restoring another manager.",
                )

        if role_code == "team_lead":
            # The active, team-scoped TEAM_LEAD role is authoritative for routing.
            # Check it separately from membership_type so legacy member-type rows
            # cannot allow a second active Team Lead for the same team.
            existing_team_lead_role = db.execute(
                text("""
                    SELECT ura.user_id
                    FROM public.user_role_assignments ura
                    JOIN public.roles r ON r.id = ura.role_id
                    JOIN public.users u ON u.id = ura.user_id AND u.company_id = ura.company_id
                    WHERE ura.company_id = :cid
                      AND ura.scope_type = 'team'
                      AND ura.scope_team_id = :tid
                      AND ura.revoked_at IS NULL
                      AND lower(r.code) = 'team_lead'
                      AND r.is_active = TRUE
                      AND u.status = 'active'
                      AND u.deleted_at IS NULL
                      AND u.id <> :uid
                    LIMIT 1
                """),
                {"cid": company_id, "tid": team_id, "uid": user_id},
            ).scalar_one_or_none()
            if existing_team_lead_role:
                raise HTTPException(status_code=409, detail="The selected team already has an active Team Lead role assignment.")

            existing_lead = db.execute(
                text("""
                    SELECT 1 FROM public.team_members
                    WHERE company_id = :cid AND team_id = :tid
                      AND membership_type = 'lead' AND left_at IS NULL
                      AND user_id <> :uid
                    LIMIT 1
                """),
                {"cid": company_id, "tid": team_id, "uid": user_id},
            ).scalar_one_or_none()
            if existing_lead:
                raise HTTPException(status_code=409, detail="The selected team already has an active Team Lead.")

        scope_type, scope_department_id, scope_team_id = _compute_scope(
            role_code, department_id, team_id
        )
        # Restore the existing identity. Never recreate the user or re-enable old role history.
        db.execute(
            text("""
                UPDATE public.users
                SET status = 'active', deleted_at = NULL,
                    department_id = :department_id, updated_at = NOW()
                WHERE id = :uid AND company_id = :cid AND deleted_at IS NOT NULL
            """),
            {"department_id": department_id, "uid": user_id, "cid": company_id},
        )

        # Do not revive credentials or user-specific domain grants from before deletion.
        db.execute(
            text("UPDATE public.user_sessions SET revoked_at = COALESCE(revoked_at, NOW()) WHERE user_id = :uid AND company_id = :cid AND revoked_at IS NULL"),
            {"uid": user_id, "cid": company_id},
        )
        db.execute(
            text("UPDATE public.user_domains SET revoked_at = COALESCE(revoked_at, NOW()) WHERE user_id = :uid AND company_id = :cid AND revoked_at IS NULL"),
            {"uid": user_id, "cid": company_id},
        )

        if team_id:
            db.execute(
                text("""
                    INSERT INTO public.team_members
                        (company_id, team_id, user_id, membership_type, is_primary)
                    VALUES (:cid, :tid, :uid, :membership_type, TRUE)
                """),
                {
                    "cid": company_id,
                    "tid": team_id,
                    "uid": user_id,
                    "membership_type": _team_membership_type(role_code),
                },
            )

        db.execute(
            text("""
                INSERT INTO public.user_role_assignments
                    (user_id, role_id, company_id, scope_type, scope_department_id, scope_team_id)
                VALUES (:uid, :rid, :cid, :scope_type, :scope_department_id, :scope_team_id)
            """),
            {
                "uid": user_id,
                "rid": role["id"],
                "cid": company_id,
                "scope_type": scope_type,
                "scope_department_id": scope_department_id,
                "scope_team_id": scope_team_id,
            },
        )

        write_audit_event(
            db,
            company_id=company_id,
            action="user.restore",
            entity_type="user",
            entity_id=user_id,
            metadata={
                "restored_by": str(current_user.id),
                "role_code": role_code,
                "scope_type": scope_type,
                "department_id": str(department_id) if department_id else None,
                "team_id": str(team_id) if team_id else None,
                "old_role_assignments_preserved_as_revoked": True,
                "old_sessions_revoked": True,
                "old_user_domain_grants_revoked": True,
            },
        )
        db.commit()

        return SysUserListItem(
            id=target["id"], company_id=company_id, company_name=company["name"],
            employee_code=target["employee_code"], first_name=target["first_name"],
            last_name=target["last_name"], email=target["email"],
            department_id=department_id, department_name=department_name,
            team_id=team_id, team_name=team_name, primary_role=role_code,
            status="active", last_login_at=target["last_login_at"],
            created_at=target["created_at"], deleted_at=None,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        error_text = str(exc).lower()
        if "uq_one_active_team_lead" in error_text:
            raise HTTPException(status_code=409, detail="The selected team already has an active Team Lead.") from exc
        if "row-level security" in error_text or "violates row-level security policy" in error_text:
            raise HTTPException(status_code=403, detail="Insufficient permission to restore this user.") from exc
        if "duplicate key" in error_text or "unique constraint" in error_text:
            raise HTTPException(status_code=409, detail="The requested organizational role conflicts with an existing active assignment.") from exc
        raise HTTPException(status_code=500, detail="Failed to restore the user. No changes were committed.") from exc

def sys_list_roles(db: Session) -> list[RoleItem]:
    rows = db.execute(
        text("SELECT id, lower(code) AS code, name, is_active FROM public.roles WHERE is_active = TRUE ORDER BY code"),
    ).mappings().all()
    return [RoleItem(id=r["id"], code=r["code"], name=r["name"], is_active=r["is_active"]) for r in rows]
