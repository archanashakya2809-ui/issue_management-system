from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlatformSettingsBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_name: str = Field(min_length=1, max_length=150)
    default_sla_hours: int = Field(ge=1, le=8760)
    session_timeout_minutes: int = Field(ge=5, le=1440)
    max_login_attempts: int = Field(ge=3, le=20)
    lockout_minutes: int = Field(ge=1, le=1440)
    password_policy: Literal["Standard", "Strong", "Strict"]
    maintenance_mode: bool
    email_notifications: bool
    system_alerts: bool

    @field_validator("app_name")
    @classmethod
    def normalize_app_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Application name cannot be blank.")
        return value


class PlatformSettingsUpdate(PlatformSettingsBase):
    pass


class PlatformSettingsRead(PlatformSettingsBase):
    pass
