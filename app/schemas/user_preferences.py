from pydantic import BaseModel, ConfigDict


class UserPreferencesRead(BaseModel):
    """Personal notification and appearance preferences for the current user."""

    model_config = ConfigDict(extra="forbid")

    in_app: bool = True
    email: bool = True
    weekly_summary: bool = False
    compact_tables: bool = False


class UserPreferencesUpdate(UserPreferencesRead):
    """Complete replacement payload for the current user's preferences."""

    pass
