import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings


def test_access_token_defaults_are_hardened() -> None:
    assert Settings(_env_file=None).access_token_minutes == 10
    assert settings.access_token_issuer == "ticketsys-api"
    assert settings.access_token_audience == "ticketsys-web"
    assert settings.access_token_kid == "v1"


def test_development_can_use_legacy_secret_as_temporary_fallback() -> None:
    config = Settings(
        app_env="development",
        secret_key="d" * 48,
        access_token_signing_key=None,
    )
    assert config.effective_access_token_signing_key == "d" * 48


def test_production_requires_dedicated_access_signing_key() -> None:
    with pytest.raises(ValidationError, match="ACCESS_TOKEN_SIGNING_KEY"):
        Settings(
            app_env="production",
            secret_key="s" * 48,
            access_token_signing_key=None,
            refresh_cookie_secure=True,
        )


def test_production_rejects_example_signing_key() -> None:
    with pytest.raises(ValidationError, match="Replace the ACCESS_TOKEN_SIGNING_KEY"):
        Settings(
            app_env="production",
            secret_key="s" * 48,
            access_token_signing_key="replace-with-" + "x" * 48,
            refresh_cookie_secure=True,
        )


def test_production_accepts_dedicated_signing_key() -> None:
    config = Settings(
        app_env="production",
        secret_key="s" * 48,
        access_token_signing_key="a" * 48,
        refresh_cookie_secure=True,
    )
    assert config.effective_access_token_signing_key == "a" * 48


def test_active_key_id_cannot_be_repeated_in_previous_key_map() -> None:
    with pytest.raises(ValidationError, match="cannot also appear"):
        Settings(
            app_env="development",
            access_token_kid="v1",
            access_token_previous_signing_keys={"v1": "p" * 48},
        )


def test_previous_keys_must_be_at_least_32_characters() -> None:
    with pytest.raises(ValidationError, match="must be at least 32 characters"):
        Settings(
            app_env="development",
            access_token_previous_signing_keys={"v0": "too-short"},
        )
