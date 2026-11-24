from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field as PField


# ── Read schemas (existing) ─────────────────────────────────────────────────

class DepartmentItem(BaseModel):
    id: UUID
    name: str
    status: str
    lead_name: str | None
    team_count: int


class TeamItem(BaseModel):
    id: UUID
    name: str
    department_id: UUID
    department_name: str | None
    status: str
    lead_name: str | None
    member_count: int


class DomainItem(BaseModel):
    id: UUID
    name: str
    department_id: UUID
    department_name: str | None
    status: str
    category_count: int


class CategoryItem(BaseModel):
    id: UUID
    name: str
    domain_id: UUID
    domain_name: str | None
    department_id: UUID | None
    is_active: bool


class CompanyItem(BaseModel):
    id: UUID
    name: str
    code: str
    status: str
    user_count: int
    department_count: int
    created_at: datetime

class CompanyCreate(BaseModel):
    name: str = PField(min_length=2, max_length=150)
    code: str = PField(min_length=2, max_length=50)


class CompanyUpdate(BaseModel):
    name: str | None = PField(default=None, min_length=2, max_length=150)
    code: str | None = PField(default=None, min_length=2, max_length=50)
    status: Literal["active", "inactive"] | None = None


# ── Write schemas ────────────────────────────────────────────────────────────

class DepartmentCreate(BaseModel):
    name: str = PField(min_length=2, max_length=150)
    description: str | None = None


class DepartmentUpdate(BaseModel):
    name: str | None = PField(default=None, min_length=2, max_length=150)
    description: str | None = None
    status: Literal["active", "inactive"] | None = None


class TeamCreate(BaseModel):
    name: str = PField(min_length=2, max_length=150)
    department_id: UUID
    description: str | None = None


class TeamUpdate(BaseModel):
    name: str | None = PField(default=None, min_length=2, max_length=150)
    status: Literal["active", "inactive"] | None = None


class DomainCreate(BaseModel):
    name: str = PField(min_length=2, max_length=150)
    department_id: UUID
    description: str | None = None


class DomainUpdate(BaseModel):
    name: str | None = PField(default=None, min_length=2, max_length=150)
    status: Literal["active", "inactive"] | None = None


class CategoryCreate(BaseModel):
    name: str = PField(min_length=2, max_length=150)
    domain_id: UUID
    description: str | None = None


class CategoryUpdate(BaseModel):
    name: str | None = PField(default=None, min_length=2, max_length=150)
    is_active: bool | None = None


# ── Company-admin user management (tenant-scoped, no company_id in payload) ──

class OrgUserCreate(BaseModel):
    """User creation payload for Company Admin. company_id is always derived from auth."""
    first_name: str = PField(min_length=1, max_length=100)
    last_name: str | None = PField(default=None, max_length=100)
    email: EmailStr
    employee_code: str | None = PField(default=None, max_length=50)
    department_id: UUID | None = None
    team_id: UUID | None = None
    role_code: str
    password: str = PField(min_length=8, max_length=128)
    status: Literal["active", "pending"] = "active"


class OrgUserUpdate(BaseModel):
    """User update payload for Company Admin. company_id is always derived from auth."""
    first_name: str | None = PField(default=None, min_length=1, max_length=100)
    last_name: str | None = PField(default=None, max_length=100)
    employee_code: str | None = PField(default=None, max_length=50)
    department_id: UUID | None = None
    team_id: UUID | None = None
    role_code: str | None = None
    status: Literal["active", "inactive", "locked"] | None = None
