"""
Integration tests for the authentication login flow.

These tests verify:
  1.  Newly created Team Lead accounts can log in.
  2.  Newly created Company Admin accounts can log in.
  3.  Incorrect password is rejected.
  4.  Non-existent email is rejected.
  5.  Invalid company code is rejected.
  6.  Inactive user is rejected.
  7.  Inactive company is rejected.
  8.  Soft-deleted user is rejected.
  9.  Valid JWT grants access to /api/v1/auth/me.
  10. Invalid or expired JWT is rejected by /api/v1/auth/me.
  11. Correct role and company context is returned after login.
  12. Regression: user with a legacy $2b$ hash (created before the fix) can
      now authenticate after the V007 migration.

Test data is created by the session-scoped fixtures in conftest.py using a
superuser database connection.  The TestClient exercises the full application
stack — RLS, JWT, auth endpoint — for every assertion.
"""

import pytest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt

from app.core.config import settings
from app.core.security import ALGORITHM

LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _login(client, company_code: str, email: str, password: str):
    return client.post(LOGIN_URL, json={
        "company_code": company_code,
        "email": email,
        "password": password,
    })


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1 & 2.  Successful login for newly created Team Lead and Company Admin
# ---------------------------------------------------------------------------

def test_team_lead_can_login(api_client, team_lead_creds):
    """TC-01: Newly created Team Lead authenticates successfully."""
    resp = _login(api_client, **team_lead_creds)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "access_token" in body
    assert body["access_token"]
    assert body["expires_in_seconds"] > 0
    assert body["user"]["email"] == team_lead_creds["email"].lower()


def test_company_admin_can_login(api_client, company_admin_creds):
    """TC-02: Newly created Company Admin authenticates successfully."""
    resp = _login(api_client, **company_admin_creds)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "access_token" in body
    assert body["user"]["email"] == company_admin_creds["email"].lower()


# ---------------------------------------------------------------------------
# 3.  Incorrect password rejection
# ---------------------------------------------------------------------------

def test_wrong_password_is_rejected(api_client, team_lead_creds):
    """TC-03: Correct email/company but wrong password → 401."""
    resp = _login(api_client,
                  company_code=team_lead_creds["company_code"],
                  email=team_lead_creds["email"],
                  password="CompletelyWrongPassword999!")
    assert resp.status_code == 401
    assert "access_token" not in resp.json()


# ---------------------------------------------------------------------------
# 4.  Invalid email rejection
# ---------------------------------------------------------------------------

def test_invalid_email_is_rejected(api_client, team_lead_creds):
    """TC-04: Non-existent email in a real company → 401."""
    resp = _login(api_client,
                  company_code=team_lead_creds["company_code"],
                  email="nobody@example.com",
                  password=team_lead_creds["password"])
    assert resp.status_code == 401
    assert "access_token" not in resp.json()


# ---------------------------------------------------------------------------
# 5.  Invalid company code rejection
# ---------------------------------------------------------------------------

def test_invalid_company_code_is_rejected(api_client):
    """TC-05: Non-existent company code → 401."""
    resp = _login(api_client,
                  company_code="DOESNOTEXIST999",
                  email="nobody@example.com",
                  password="AnyPassword1!")
    assert resp.status_code == 401
    assert "access_token" not in resp.json()


# ---------------------------------------------------------------------------
# 6.  Inactive user rejection
# ---------------------------------------------------------------------------

def test_inactive_user_is_rejected(api_client, inactive_user_creds):
    """TC-06: User with status='inactive' cannot authenticate."""
    resp = _login(api_client, **inactive_user_creds)
    assert resp.status_code == 401
    assert "access_token" not in resp.json()


# ---------------------------------------------------------------------------
# 7.  Inactive company rejection
# ---------------------------------------------------------------------------

def test_inactive_company_is_rejected(api_client, inactive_company_creds):
    """TC-07: Active user in an inactive company cannot authenticate."""
    resp = _login(api_client, **inactive_company_creds)
    assert resp.status_code == 401
    assert "access_token" not in resp.json()


# ---------------------------------------------------------------------------
# 8.  Soft-deleted user rejection
# ---------------------------------------------------------------------------

def test_soft_deleted_user_is_rejected(api_client, deleted_user_creds):
    """TC-08: Soft-deleted user (deleted_at IS NOT NULL) cannot authenticate."""
    resp = _login(api_client, **deleted_user_creds)
    assert resp.status_code == 401
    assert "access_token" not in resp.json()


# ---------------------------------------------------------------------------
# 9.  Valid JWT grants /auth/me access
# ---------------------------------------------------------------------------

def test_valid_jwt_grants_me_access(api_client, company_admin_creds):
    """TC-09: Token obtained at login gives a 200 from /auth/me."""
    login_resp = _login(api_client, **company_admin_creds)
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    me_resp = api_client.get(ME_URL, headers=_auth_header(token))
    assert me_resp.status_code == 200
    body = me_resp.json()
    assert body["email"] == company_admin_creds["email"].lower()


# ---------------------------------------------------------------------------
# 10. Invalid / expired JWT rejection
# ---------------------------------------------------------------------------

def test_invalid_jwt_is_rejected(api_client):
    """TC-10a: Completely malformed JWT → 401."""
    resp = api_client.get(ME_URL, headers=_auth_header("not.a.real.token"))
    assert resp.status_code == 401


def test_expired_jwt_is_rejected(api_client):
    """TC-10b: Structurally valid but expired JWT → 401."""
    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": str(uuid4()),
            "company_id": str(uuid4()),
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
        },
        settings.secret_key,
        algorithm=ALGORITHM,
    )
    resp = api_client.get(ME_URL, headers=_auth_header(expired))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 11. Correct role and company context after login
# ---------------------------------------------------------------------------

def test_team_lead_role_context(api_client, team_lead_creds):
    """TC-11a: /auth/me returns team_lead role for the Team Lead user."""
    login_resp = _login(api_client, **team_lead_creds)
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    me_resp = api_client.get(ME_URL, headers=_auth_header(token))
    assert me_resp.status_code == 200
    body = me_resp.json()

    role_codes = [r["role_code"] for r in body.get("roles", [])]
    assert "team_lead" in role_codes, f"Expected team_lead in {role_codes}"


def test_company_admin_role_context(api_client, company_admin_creds):
    """TC-11b: /auth/me returns company_admin role for the Company Admin user."""
    login_resp = _login(api_client, **company_admin_creds)
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    me_resp = api_client.get(ME_URL, headers=_auth_header(token))
    assert me_resp.status_code == 200
    body = me_resp.json()

    role_codes = [r["role_code"] for r in body.get("roles", [])]
    assert "company_admin" in role_codes, f"Expected company_admin in {role_codes}"


# ---------------------------------------------------------------------------
# 12. Regression: legacy $2b$ hash user (the original bug)
# ---------------------------------------------------------------------------

def test_legacy_2b_hash_user_can_now_login(api_client, legacy_2b_creds):
    """TC-12: User whose password was stored with a $2b$ hash (pre-fix) can
    now authenticate after the V007 migration updated app_authenticate_user
    to normalise the prefix before calling pgcrypto.crypt().
    """
    resp = _login(api_client, **legacy_2b_creds)
    assert resp.status_code == 200, (
        f"Expected 200 but got {resp.status_code}. "
        "The V007 migration may not have been applied or the $2b$ normalisation "
        "in app_authenticate_user is not working correctly."
    )
    assert "access_token" in resp.json()
