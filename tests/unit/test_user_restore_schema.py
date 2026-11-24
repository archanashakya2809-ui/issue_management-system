from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.system_admin import SysUserRestore


def test_restore_requires_explicit_role():
    with pytest.raises(ValidationError):
        SysUserRestore.model_validate({"department_id": str(uuid4())})


def test_department_manager_restore_payload():
    department_id = uuid4()
    payload = SysUserRestore(
        role_code="department_manager",
        department_id=department_id,
    )
    assert payload.role_code == "department_manager"
    assert payload.department_id == department_id
    assert payload.team_id is None


def test_team_scoped_restore_payload_supports_team_id():
    department_id = uuid4()
    team_id = uuid4()
    payload = SysUserRestore(
        role_code="support_agent",
        department_id=department_id,
        team_id=team_id,
    )
    assert payload.department_id == department_id
    assert payload.team_id == team_id
