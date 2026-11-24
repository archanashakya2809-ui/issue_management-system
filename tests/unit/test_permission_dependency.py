
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import deps


def test_permission_dependency_allows_authorized_user(monkeypatch) -> None:
    permission_check = Mock(return_value=True)
    monkeypatch.setattr(deps, "user_has_permission", permission_check)

    user = SimpleNamespace(id=uuid4(), company_id=uuid4())
    fake_db = object()

    dependency = deps.require_permission("ticket.assign")
    result = dependency(db=fake_db, current_user=user)

    assert result is user
    permission_check.assert_called_once_with(
        fake_db,
        user_id=user.id,
        company_id=user.company_id,
        permission_code="ticket.assign",
    )


def test_permission_dependency_rejects_unauthorized_user(monkeypatch) -> None:
    permission_check = Mock(return_value=False)
    monkeypatch.setattr(deps, "user_has_permission", permission_check)

    user = SimpleNamespace(id=uuid4(), company_id=uuid4())
    dependency = deps.require_permission("ticket.assign")

    with pytest.raises(HTTPException) as exc_info:
        dependency(db=object(), current_user=user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Permission required: ticket.assign"
