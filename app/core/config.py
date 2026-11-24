from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    app_name: str = "Ticket Management API"
    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"
    # SECRET_KEY remains for unrelated legacy application uses. JWT signing uses
    # ACCESS_TOKEN_SIGNING_KEY, which must be configured explicitly in production.
    secret_key: str = Field(
        default="development-only-secret-key-change-before-use-123456", min_length=32
    )
    access_token_signing_key: str | None = Field(default=None, min_length=32)
    access_token_kid: str = Field(default="v1", min_length=1, max_length=64)
    # JSON object mapping retired key IDs to their signing keys, for controlled
    # verification during key rotation. Example: {"v0":"<old-64-char-key>"}
    access_token_previous_signing_keys: dict[str, str] = Field(default_factory=dict)
    access_token_issuer: str = Field(default="ticketsys-api", min_length=1)
    access_token_audience: str = Field(default="ticketsys-web", min_length=1)
    access_token_minutes: int = Field(default=10, ge=5, le=1440)
    # Refresh tokens are opaque, single-use credentials stored only as hashes.
    # The lifetime is absolute per login family and is not extended by rotation.
    refresh_token_days: int = Field(default=14, ge=1, le=90)
    refresh_cookie_name: str = "ticketsys_refresh"
    refresh_csrf_cookie_name: str = "ticketsys_refresh_csrf"
    refresh_cookie_secure: bool | None = None
    refresh_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    database_url: str = "postgresql+psycopg://ticket_app:TicketApp_ChangeMe_2026!@localhost:5433/ticket"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"
    # Set a high-entropy token to enable external health-monitor event ingestion.
    # If empty, POST /health/incidents returns 503 and remains disabled.
    health_ingest_token: str = ""

    # Private ticket attachment storage and upload limits. These map to the
    # ATTACHMENT_* variables already present in .env/.env.example.
    attachment_storage_root: Path = Path("./private_uploads")
    attachment_max_file_size_bytes: int = Field(
        default=10 * 1024 * 1024, ge=1024
    )
    attachment_max_files_per_ticket: int = Field(default=5, ge=1, le=20)

    @field_validator("secret_key")
    @classmethod
    def reject_placeholder_in_production(cls, value: str, info):
        if (
            info.data.get("app_env", "development").lower() == "production"
            and value.startswith("development-only")
        ):
            raise ValueError("Set SECRET_KEY to a strong random value in production.")
        return value

    @field_validator("access_token_signing_key")
    @classmethod
    def reject_placeholder_access_signing_key(cls, value: str | None, info):
        if (
            info.data.get("app_env", "development").lower() == "production"
            and value
            and value.lower().startswith("replace-with-")
        ):
            raise ValueError("Replace the ACCESS_TOKEN_SIGNING_KEY example value with a generated secret.")
        return value

    @field_validator("access_token_previous_signing_keys")
    @classmethod
    def validate_previous_signing_keys(cls, value: dict[str, str]):
        for key_id, key in value.items():
            if not key_id or len(key_id) > 64:
                raise ValueError("Previous access-token key IDs must be 1-64 characters.")
            if len(key) < 32:
                raise ValueError(f"Previous signing key '{key_id}' must be at least 32 characters.")
        return value

    @field_validator("refresh_cookie_samesite")
    @classmethod
    def require_secure_for_none_samesite(cls, value: str, info):
        if value == "none" and info.data.get("refresh_cookie_secure") is False:
            raise ValueError("REFRESH_COOKIE_SECURE must be true when SameSite=None.")
        return value

    @model_validator(mode="after")
    def validate_security_deployment(self):
        if self.app_env.lower() == "production" and not self.access_token_signing_key:
            raise ValueError("ACCESS_TOKEN_SIGNING_KEY must be explicitly configured in production.")
        if (
            self.app_env.lower() == "production"
            and self.access_token_signing_key
            and self.access_token_signing_key.lower().startswith("replace-with-")
        ):
            raise ValueError("Replace the ACCESS_TOKEN_SIGNING_KEY example value with a generated secret.")
        if self.access_token_signing_key and len(self.access_token_signing_key) < 32:
            raise ValueError("ACCESS_TOKEN_SIGNING_KEY must be at least 32 characters.")
        if self.access_token_kid in self.access_token_previous_signing_keys:
            raise ValueError("ACCESS_TOKEN_KID cannot also appear in ACCESS_TOKEN_PREVIOUS_SIGNING_KEYS.")
        if self.app_env.lower() == "production" and not self.effective_refresh_cookie_secure:
            raise ValueError("REFRESH_COOKIE_SECURE must be true in production.")
        if self.refresh_cookie_samesite == "none" and not self.effective_refresh_cookie_secure:
            raise ValueError("SameSite=None requires secure refresh cookies.")
        return self

    @property
    def effective_access_token_signing_key(self) -> str:
        # Development fallback preserves local startup. Production validation
        # requires a dedicated ACCESS_TOKEN_SIGNING_KEY.
        return self.access_token_signing_key or self.secret_key

    @property
    def effective_refresh_cookie_secure(self) -> bool:
        if self.refresh_cookie_secure is not None:
            return self.refresh_cookie_secure
        return self.app_env.lower() == "production"

    @property
    def refresh_cookie_path(self) -> str:
        return f"{self.api_v1_prefix.rstrip('/')}/auth"

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
