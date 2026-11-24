
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.config import settings
from app.core.security import ALGORITHM, create_access_token
from app.main import app


class FakeSession:
    """Database stub for authentication failures; never connects to PostgreSQL."""

    def execute(self, *_args, **_kwargs):
        return None

    def scalar(self, *_args, **_kwargs):
        # Simulates no active session being found.
        return None


@pytest.fixture
def api_client():
    fake_db = FakeSession()

    def override_get_db():
        yield fake_db

    original_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)


def test_me_rejects_missing_token(api_client):
    response = api_client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_malformed_token(api_client):
    response = api_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-valid-jwt"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired access token"


def test_me_rejects_expired_token(api_client):
    now = datetime.now(UTC)

    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "company_id": str(uuid4()),
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
        },
        settings.secret_key,
        algorithm=ALGORITHM,
    )

    response = api_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired access token"


def test_me_rejects_token_without_active_session(api_client):
    user_id = uuid4()
    company_id = uuid4()
    token, _ = create_access_token(user_id, company_id)

    response = api_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Session is no longer active"

@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/v1/tickets", "GET"),
        ("/api/v1/tickets/d1111111-1111-1111-1111-111111111111", "GET"),
        (
            "/api/v1/tickets/d1111111-1111-1111-1111-111111111111/comments",
            "GET",
        ),
    ],
)
def test_protected_ticket_endpoints_reject_missing_token(
    api_client, path: str, method: str
) -> None:
    response = api_client.request(method, path)

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
