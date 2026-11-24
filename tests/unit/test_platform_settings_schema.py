import pytest
from pydantic import ValidationError

from app.schemas.platform_settings import PlatformSettingsUpdate


def valid_payload() -> dict:
    return {
        "app_name": "Ticket Management System",
        "default_sla_hours": 48,
        "session_timeout_minutes": 60,
        "max_login_attempts": 5,
        "lockout_minutes": 30,
        "password_policy": "Strong",
        "maintenance_mode": False,
        "email_notifications": True,
        "system_alerts": True,
    }


def test_platform_settings_accepts_valid_payload():
    parsed = PlatformSettingsUpdate.model_validate(valid_payload())
    assert parsed.app_name == "Ticket Management System"
    assert parsed.max_login_attempts == 5


@pytest.mark.parametrize(
    "field,value",
    [
        ("default_sla_hours", 0),
        ("default_sla_hours", 8761),
        ("session_timeout_minutes", 4),
        ("max_login_attempts", 2),
        ("lockout_minutes", 1441),
        ("password_policy", "Unrestricted"),
    ],
)
def test_platform_settings_rejects_out_of_range_values(field, value):
    payload = valid_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        PlatformSettingsUpdate.model_validate(payload)


def test_platform_settings_rejects_unknown_fields():
    payload = valid_payload()
    payload["company_id"] = "00000000-0000-0000-0000-000000000001"
    with pytest.raises(ValidationError):
        PlatformSettingsUpdate.model_validate(payload)
