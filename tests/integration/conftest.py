"""
Integration test fixtures.

Test data is inserted via a superuser connection that bypasses application RLS
so that every test uses fully isolated, deterministic data.  The application
stack (RLS, JWT, API endpoints) is exercised through the normal TestClient
path — only setup and teardown use the superuser.

Required environment (defaults work for a local PostgreSQL setup):
  TEST_SUPERUSER_URL  superuser psycopg connection string
                      default: postgresql+psycopg://postgres:admin@localhost:5433/ticket

Role constraints (enforced by validate_user_role_assignment_scope trigger):
  TEAM_LEAD    → scope_type = 'team', needs team + dept + team_member row
  COMPANY_ADMIN → scope_type = 'company'
  SYSTEM_ADMIN  → scope_type = 'system', needs is_platform company
"""

import os
import uuid

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.main import app

SUPERUSER_URL = os.getenv(
    "TEST_SUPERUSER_URL",
    "postgresql+psycopg://postgres:admin@localhost:5433/ticket",
)

# Stable role UUIDs from seed data.
_ROLE_TEAM_LEAD = "61111111-1111-1111-1111-111111111113"
_ROLE_COMPANY_ADMIN = "61111111-1111-1111-1111-111111111115"


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

def _2a_hash(password: str) -> str:
    """Produce a pgcrypto-compatible $2a$ bcrypt hash (post-fix format)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(prefix=b"2a")).decode()


def _2b_hash(password: str) -> str:
    """Produce a legacy $2b$ bcrypt hash (pre-fix format — triggers the bug)."""
    h = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    assert h.startswith("$2b$")
    return h


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _code(prefix: str, base: str) -> str:
    return (prefix + base.replace("-", "").upper())[:20]


def _insert_company(conn, *, status: str = "active") -> tuple[str, str]:
    cid, code = _uid(), _uid()
    conn.execute(text("""
        INSERT INTO public.companies (id, name, code, status, is_platform)
        VALUES (:id, :name, :code, :status, FALSE)
    """), {"id": cid, "name": f"TestCo {cid[:8]}", "code": _code("TST", code), "status": status})
    return cid, _code("TST", code)


def _insert_dept(conn, company_id: str) -> str:
    did = _uid()
    conn.execute(text("""
        INSERT INTO public.departments (id, company_id, name, code, status)
        VALUES (:id, :cid, 'Test Dept', :code, 'active')
    """), {"id": did, "cid": company_id, "code": _code("DEPT", did)})
    return did


def _insert_team(conn, company_id: str, dept_id: str) -> str:
    tid = _uid()
    conn.execute(text("""
        INSERT INTO public.teams (id, company_id, department_id, name, code, status)
        VALUES (:id, :cid, :did, 'Test Team', :code, 'active')
    """), {"id": tid, "cid": company_id, "did": dept_id, "code": _code("TEAM", tid)})
    return tid


def _insert_user(conn, *, company_id: str, email: str, pw_hash: str,
                 first_name: str, dept_id: str | None = None,
                 status: str = "active", deleted: bool = False) -> str:
    uid = _uid()
    conn.execute(text("""
        INSERT INTO public.users
            (id, company_id, first_name, email, password_hash, department_id,
             status, deleted_at)
        VALUES
            (:id, :cid, :fn, :email, :pw, :dept,
             :status, CASE WHEN :del THEN NOW() ELSE NULL END)
    """), {"id": uid, "cid": company_id, "fn": first_name,
           "email": email, "pw": pw_hash, "dept": dept_id,
           "status": status, "del": deleted})
    return uid


def _assign_team_lead(conn, *, user_id: str, company_id: str, team_id: str) -> None:
    """Insert team_member row then URA with team scope."""
    conn.execute(text("""
        INSERT INTO public.team_members
            (company_id, team_id, user_id, membership_type, is_primary)
        VALUES (:cid, :tid, :uid, 'member', TRUE)
    """), {"cid": company_id, "tid": team_id, "uid": user_id})
    conn.execute(text("""
        INSERT INTO public.user_role_assignments
            (user_id, role_id, company_id, scope_type, scope_team_id)
        VALUES (:uid, :rid, :cid, 'team', :team_id)
    """), {"uid": user_id, "rid": _ROLE_TEAM_LEAD,
           "cid": company_id, "team_id": team_id})


def _assign_company_admin(conn, *, user_id: str, company_id: str) -> None:
    conn.execute(text("""
        INSERT INTO public.user_role_assignments
            (user_id, role_id, company_id, scope_type)
        VALUES (:uid, :rid, :cid, 'company')
    """), {"uid": user_id, "rid": _ROLE_COMPANY_ADMIN, "cid": company_id})


def _teardown_company(conn, company_id: str) -> None:
    """Remove all test data for a company in dependency order.

    Triggers are disabled with session_replication_role = replica to avoid
    cascade trigger conflicts (e.g. revoke_team_scoped_roles_on_membership_end
    re-validates membership on DELETE which fails when users are already gone).
    """
    conn.execute(text("SET session_replication_role = 'replica'"))
    conn.execute(text("DELETE FROM public.user_sessions WHERE company_id = :c"), {"c": company_id})
    conn.execute(text("DELETE FROM public.audit_logs WHERE company_id = :c"), {"c": company_id})
    conn.execute(text("DELETE FROM public.team_members WHERE company_id = :c"), {"c": company_id})
    conn.execute(text("DELETE FROM public.user_role_assignments WHERE company_id = :c"), {"c": company_id})
    conn.execute(text("DELETE FROM public.users WHERE company_id = :c"), {"c": company_id})
    conn.execute(text("DELETE FROM public.teams WHERE company_id = :c"), {"c": company_id})
    conn.execute(text("DELETE FROM public.departments WHERE company_id = :c"), {"c": company_id})
    conn.execute(text("DELETE FROM public.companies WHERE id = :c"), {"c": company_id})
    conn.execute(text("SET session_replication_role = 'origin'"))


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def su_engine():
    engine = create_engine(SUPERUSER_URL, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def api_client():
    # Use a valid IP so user_sessions.ip_address (inet type) accepts the value.
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield client


@pytest.fixture(scope="session")
def test_company(su_engine):
    """Isolated test company with one department and one team."""
    with su_engine.connect() as conn:
        cid, code = _insert_company(conn)
        dept_id = _insert_dept(conn, cid)
        team_id = _insert_team(conn, cid, dept_id)
        conn.commit()
    yield {"id": cid, "code": code, "dept_id": dept_id, "team_id": team_id}
    with su_engine.connect() as conn:
        _teardown_company(conn, cid)
        conn.commit()


@pytest.fixture(scope="session")
def team_lead_creds(su_engine, test_company):
    """Active Team Lead with a $2a$ hash — must authenticate after the fix."""
    pw = "TeamLeadPass1!"
    with su_engine.connect() as conn:
        uid = _insert_user(conn,
                           company_id=test_company["id"],
                           email="teamlead@example.com",
                           pw_hash=_2a_hash(pw),
                           first_name="Test TeamLead",
                           dept_id=test_company["dept_id"])
        _assign_team_lead(conn,
                          user_id=uid,
                          company_id=test_company["id"],
                          team_id=test_company["team_id"])
        conn.commit()
    return {"email": "teamlead@example.com", "password": pw,
            "company_code": test_company["code"]}


@pytest.fixture(scope="session")
def company_admin_creds(su_engine, test_company):
    """Active Company Admin with a $2a$ hash — must authenticate after the fix."""
    pw = "CompanyAdminPass1!"
    with su_engine.connect() as conn:
        uid = _insert_user(conn,
                           company_id=test_company["id"],
                           email="companyadmin@example.com",
                           pw_hash=_2a_hash(pw),
                           first_name="Test CompanyAdmin")
        _assign_company_admin(conn, user_id=uid, company_id=test_company["id"])
        conn.commit()
    return {"email": "companyadmin@example.com", "password": pw,
            "company_code": test_company["code"]}


@pytest.fixture(scope="session")
def legacy_2b_creds(su_engine, test_company):
    """Active user with a legacy $2b$ hash — regression for the original bug.

    Before V007, pgcrypto.crypt() could not verify $2b$ hashes, so these users
    could never log in.  After V007 the normalisation makes it work.
    """
    pw = "Legacy2bPass1!"
    with su_engine.connect() as conn:
        uid = _insert_user(conn,
                           company_id=test_company["id"],
                           email="legacy2b@example.com",
                           pw_hash=_2b_hash(pw),
                           first_name="Test Legacy")
        _assign_company_admin(conn, user_id=uid, company_id=test_company["id"])
        conn.commit()
    return {"email": "legacy2b@example.com", "password": pw,
            "company_code": test_company["code"]}


@pytest.fixture(scope="session")
def inactive_user_creds(su_engine, test_company):
    """User with status='inactive' — authentication must be rejected."""
    pw = "InactivePass1!"
    with su_engine.connect() as conn:
        uid = _insert_user(conn,
                           company_id=test_company["id"],
                           email="inactive@example.com",
                           pw_hash=_2a_hash(pw),
                           first_name="Test Inactive",
                           status="inactive")
        _assign_company_admin(conn, user_id=uid, company_id=test_company["id"])
        conn.commit()
    return {"email": "inactive@example.com", "password": pw,
            "company_code": test_company["code"]}


@pytest.fixture(scope="session")
def deleted_user_creds(su_engine, test_company):
    """Soft-deleted user — authentication must be rejected."""
    pw = "DeletedPass1!"
    with su_engine.connect() as conn:
        uid = _insert_user(conn,
                           company_id=test_company["id"],
                           email="deleted@example.com",
                           pw_hash=_2a_hash(pw),
                           first_name="Test Deleted",
                           deleted=True)
        _assign_company_admin(conn, user_id=uid, company_id=test_company["id"])
        conn.commit()
    return {"email": "deleted@example.com", "password": pw,
            "company_code": test_company["code"]}


@pytest.fixture(scope="session")
def inactive_company_creds(su_engine):
    """Active user in a company with status='inactive' — auth must be rejected."""
    pw = "InactCoPass1!"
    with su_engine.connect() as conn:
        cid, code = _insert_company(conn, status="inactive")
        uid = _insert_user(conn,
                           company_id=cid,
                           email="user.inactco@example.com",
                           pw_hash=_2a_hash(pw),
                           first_name="Test InactCo User")
        _assign_company_admin(conn, user_id=uid, company_id=cid)
        conn.commit()
    yield {"email": "user.inactco@example.com", "password": pw, "company_code": code}
    with su_engine.connect() as conn:
        _teardown_company(conn, cid)
        conn.commit()
