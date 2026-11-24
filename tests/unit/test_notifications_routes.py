from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.v1.routes import notifications as notifications_route
from app.db.session import get_db
from app.main import app


@pytest.fixture
def notification_api(monkeypatch):
    user = SimpleNamespace(id=uuid4(), company_id=uuid4())
    app.dependency_overrides[deps.get_current_user] = lambda: user
    monkeypatch.setattr(
        notifications_route,
        "refresh_operational_notifications",
        lambda db, *, recipient_user_id: 0,
    )
    app.dependency_overrides[get_db] = lambda: object()

    with TestClient(app) as client:
        yield client, user

    app.dependency_overrides.clear()


def test_list_notifications_is_scoped_to_current_user(notification_api, monkeypatch):
    client, user = notification_api
    notification_id = uuid4()
    ticket_id = uuid4()
    created_at = datetime.now(UTC)
    expected = SimpleNamespace(
        id=notification_id,
        type="ticket_assigned",
        title="Ticket assigned",
        message="A ticket was assigned to your team.",
        created_at=created_at,
        is_read=False,
        ticket_id=ticket_id,
    )
    calls = {}
    refresh_calls = {}

    def fake_refresh(db, *, recipient_user_id):
        refresh_calls["recipient_user_id"] = recipient_user_id
        return 0

    monkeypatch.setattr(
        notifications_route,
        "refresh_operational_notifications",
        fake_refresh,
    )

    def fake_list(db, *, company_id, recipient_user_id, limit, offset):
        calls.update(
            company_id=company_id,
            recipient_user_id=recipient_user_id,
            limit=limit,
            offset=offset,
        )
        return [expected]

    monkeypatch.setattr(notifications_route, "list_notifications", fake_list)

    response = client.get("/api/v1/notifications?limit=20&offset=5")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(notification_id),
            "type": "ticket_assigned",
            "title": "Ticket assigned",
            "message": "A ticket was assigned to your team.",
            "createdAt": created_at.isoformat().replace("+00:00", "Z"),
            "read": False,
            "ticketId": str(ticket_id),
        }
    ]
    assert calls == {
        "company_id": user.company_id,
        "recipient_user_id": user.id,
        "limit": 20,
        "offset": 5,
    }
    assert refresh_calls == {"recipient_user_id": user.id}


def test_mark_one_notification_read_uses_current_user_scope(
    notification_api, monkeypatch
):
    client, user = notification_api
    notification_id = uuid4()
    calls = {}

    def fake_mark_read(db, *, company_id, recipient_user_id, notification_id):
        calls.update(
            company_id=company_id,
            recipient_user_id=recipient_user_id,
            notification_id=notification_id,
        )

    monkeypatch.setattr(
        notifications_route, "mark_notification_read", fake_mark_read
    )

    response = client.patch(f"/api/v1/notifications/{notification_id}/read")

    assert response.status_code == 204
    assert calls == {
        "company_id": user.company_id,
        "recipient_user_id": user.id,
        "notification_id": notification_id,
    }


def test_mark_all_notifications_read_returns_updated_count(
    notification_api, monkeypatch
):
    client, user = notification_api
    calls = {}

    def fake_mark_all(db, *, company_id, recipient_user_id):
        calls.update(company_id=company_id, recipient_user_id=recipient_user_id)
        return 3

    monkeypatch.setattr(
        notifications_route, "mark_all_notifications_read", fake_mark_all
    )

    response = client.patch("/api/v1/notifications/read-all")

    assert response.status_code == 200
    assert response.json() == {"updated": 3}
    assert calls == {
        "company_id": user.company_id,
        "recipient_user_id": user.id,
    }


def test_notifications_require_authentication():
    client = TestClient(app)
    response = client.get("/api/v1/notifications")
    assert response.status_code == 401
