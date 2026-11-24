from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field as PField


# ── Workflow Statuses ─────────────────────────────────────────────────────────

class WorkflowStatusItem(BaseModel):
    id: UUID
    code: str
    name: str
    is_active: bool
    is_initial: bool
    is_terminal: bool
    company_name: str | None = None


class WorkflowStatusUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None


# ── Workflow Transitions ──────────────────────────────────────────────────────

class WorkflowTransitionItem(BaseModel):
    id: UUID
    transition_code: str | None = None
    from_status_id: UUID
    from_status_code: str
    to_status_id: UUID
    to_status_code: str
    required_permission: str | None
    required_role_code: str | None
    is_active: bool
    company_name: str | None = None


class WorkflowTransitionCreate(BaseModel):
    from_status_id: UUID
    to_status_id: UUID
    required_permission: str | None = None
    required_role_code: str | None = None


class WorkflowTransitionUpdate(BaseModel):
    is_active: bool | None = None
    required_permission: str | None = None


# ── Audit Logs ────────────────────────────────────────────────────────────────

class AuditLogItem(BaseModel):
    id: UUID
    company_id: UUID
    company_name: str | None
    actor_user_id: UUID | None
    actor_name: str | None
    action: str
    entity_type: str | None
    entity_id: UUID | None
    ip_address: str | None = None
    outcome: str
    metadata: dict | None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogItem]
    total: int


# ── Platform Administrators ───────────────────────────────────────────────────

class PlatformAdminItem(BaseModel):
    id: UUID
    name: str
    email: str
    status: str
    role_name: str
    last_login: datetime | None


# ── System-admin org management (cross-tenant) ───────────────────────────────

class SysOrgDeptCreate(BaseModel):
    company_id: UUID
    name: str = PField(min_length=2, max_length=150)
    description: str | None = None


class SysOrgDeptUpdate(BaseModel):
    name: str | None = PField(default=None, min_length=2, max_length=150)
    description: str | None = None
    status: Literal["active", "inactive"] | None = None


class SysOrgTeamCreate(BaseModel):
    company_id: UUID
    name: str = PField(min_length=2, max_length=150)
    department_id: UUID
    description: str | None = None


class SysOrgTeamUpdate(BaseModel):
    name: str | None = PField(default=None, min_length=2, max_length=150)
    status: Literal["active", "inactive"] | None = None


class SysOrgDomainCreate(BaseModel):
    company_id: UUID
    name: str = PField(min_length=2, max_length=150)
    department_id: UUID
    description: str | None = None


class SysOrgDomainUpdate(BaseModel):
    name: str | None = PField(default=None, min_length=2, max_length=150)
    status: Literal["active", "inactive"] | None = None


class SysOrgCategoryCreate(BaseModel):
    company_id: UUID
    name: str = PField(min_length=2, max_length=150)
    domain_id: UUID
    description: str | None = None


class SysOrgCategoryUpdate(BaseModel):
    name: str | None = PField(default=None, min_length=2, max_length=150)
    is_active: bool | None = None


# ── System-admin user management ─────────────────────────────────────────────

class RoleItem(BaseModel):
    id: UUID
    code: str
    name: str | None = None
    is_active: bool


class SysUserListItem(BaseModel):
    id: UUID
    company_id: UUID
    company_name: str
    employee_code: str | None = None
    first_name: str
    last_name: str | None = None
    email: str
    department_id: UUID | None = None
    department_name: str | None = None
    team_id: UUID | None = None
    team_name: str | None = None
    primary_role: str | None = None
    status: str
    last_login_at: datetime | None = None
    created_at: datetime
    deleted_at: datetime | None = None


class SysUserListResponse(BaseModel):
    items: list[SysUserListItem]
    total: int
    limit: int
    offset: int


class SysUserCreate(BaseModel):
    company_id: UUID
    first_name: str = PField(min_length=1, max_length=100)
    last_name: str | None = PField(default=None, max_length=100)
    email: EmailStr
    employee_code: str | None = PField(default=None, max_length=50)
    department_id: UUID | None = None
    team_id: UUID | None = None
    role_code: str
    password: str = PField(min_length=8, max_length=128)
    status: Literal["active", "pending"] = "active"


class SysUserUpdate(BaseModel):
    first_name: str | None = PField(default=None, min_length=1, max_length=100)
    last_name: str | None = PField(default=None, max_length=100)
    employee_code: str | None = PField(default=None, max_length=50)
    department_id: UUID | None = None
    team_id: UUID | None = None
    role_code: str | None = None
    status: Literal["active", "inactive", "locked"] | None = None


class SysUserRestore(BaseModel):
    """Explicit organizational scope to apply when restoring a soft-deleted user."""
    role_code: str = PField(min_length=1, max_length=50)
    department_id: UUID | None = None
    team_id: UUID | None = None


# ── System Health ─────────────────────────────────────────────────────────────

class ServiceHealthItem(BaseModel):
    service: str
    state: str
    latency_ms: float | None
    detail: str | None


class SystemHealthResponse(BaseModel):
    services: list[ServiceHealthItem]
    checked_at: str
