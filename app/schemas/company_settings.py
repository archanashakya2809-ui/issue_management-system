import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


_ALLOWED_TIMEZONES = frozenset({
    "Asia/Kolkata",
    "UTC",
    "America/New_York",
    "Europe/London",
})


class CompanySettingsRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=150)
    timezone: str = Field(min_length=1, max_length=64)
    default_sla_hours: int = Field(ge=1, le=8760)
    # Empty until a Company Admin configures the reply-to mailbox.
    reply_to: str = Field(max_length=254)
    notify_company_admins: bool


class CompanySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=150)
    timezone: str = Field(min_length=1, max_length=64)
    default_sla_hours: int = Field(ge=1, le=8760)
    reply_to: str = Field(min_length=1, max_length=254)
    notify_company_admins: bool

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Company display name cannot be blank.")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        value = value.strip()
        # Keep this list aligned with the timezone options in CompanySettingsPage.
        # It avoids requiring the third-party tzdata package on Windows hosts.
        if value not in _ALLOWED_TIMEZONES:
            raise ValueError("Select one of the supported time zones.")
        return value

    @field_validator("reply_to")
    @classmethod
    def validate_reply_to(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid support reply-to email address.")
        return value
