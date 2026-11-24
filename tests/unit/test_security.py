
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import bcrypt
import jwt
import pytest
from jwt import InvalidTokenError

from app.core.config import settings
from app.core.security import (
    ALGORITHM,
    create_access_token,
    decode_access_token,
    hash_password,
    hash_token,
    parse_user_id,
    verify_password,
)


def test_access_token_round_trip() -> None:
    user_id, company_id = uuid4(), uuid4()

    token, expires_at = create_access_token(user_id, company_id)
    claims = decode_access_token(token)
    header = jwt.get_unverified_header(token)

    assert parse_user_id(claims) == user_id
    assert claims["company_id"] == str(company_id)
    assert claims["iss"] == settings.access_token_issuer
    assert claims["aud"] == settings.access_token_audience
    assert claims["token_use"] == "access"
    assert claims["jti"]
    assert header["kid"] == settings.access_token_kid
    assert header["typ"] == "at+jwt"
    assert expires_at is not None


def _signed_access_claims(**overrides):
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid4()),
        "company_id": str(uuid4()),
        "iss": settings.access_token_issuer,
        "aud": settings.access_token_audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "jti": "test-token-id",
        "token_use": "access",
    }
    claims.update(overrides)
    return claims


def _signed_token(claims, *, key=None, kid=None, typ="at+jwt"):
    return jwt.encode(
        claims,
        key or settings.effective_access_token_signing_key,
        algorithm=ALGORITHM,
        headers={"kid": kid or settings.access_token_kid, "typ": typ},
    )


def test_decode_rejects_malformed_token() -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token("not.a.valid.jwt")


def test_decode_rejects_expired_token() -> None:
    now = datetime.now(UTC)
    token = _signed_token(
        _signed_access_claims(iat=now - timedelta(hours=2), nbf=now - timedelta(hours=2), exp=now - timedelta(hours=1))
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


def test_decode_rejects_missing_required_claim() -> None:
    claims = _signed_access_claims()
    del claims["company_id"]
    token = _signed_token(claims)
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_access_token(token)


def test_decode_rejects_wrong_issuer() -> None:
    token = _signed_token(_signed_access_claims(iss="another-api"))
    with pytest.raises(jwt.InvalidIssuerError):
        decode_access_token(token)


def test_decode_rejects_wrong_audience() -> None:
    token = _signed_token(_signed_access_claims(aud="another-client"))
    with pytest.raises(jwt.InvalidAudienceError):
        decode_access_token(token)


def test_decode_rejects_wrong_token_type() -> None:
    token = _signed_token(_signed_access_claims(), typ="JWT")
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_decode_rejects_non_access_token_use() -> None:
    token = _signed_token(_signed_access_claims(token_use="refresh"))
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_decode_rejects_invalid_company_uuid() -> None:
    token = _signed_token(_signed_access_claims(company_id="not-a-uuid"))
    with pytest.raises(InvalidTokenError, match="Invalid company_id claim"):
        decode_access_token(token)


def test_decode_rejects_unknown_key_id() -> None:
    token = _signed_token(_signed_access_claims(), kid="unknown-key")
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_decode_accepts_configured_previous_signing_key(monkeypatch) -> None:
    previous_key = "p" * 48
    monkeypatch.setattr(settings, "access_token_previous_signing_keys", {"v0": previous_key})
    token = _signed_token(_signed_access_claims(), key=previous_key, kid="v0")
    assert decode_access_token(token)["token_use"] == "access"


def test_access_token_default_lifetime_is_ten_minutes() -> None:
    from app.core.config import Settings

    assert Settings(_env_file=None).access_token_minutes == 10


def test_parse_user_id_rejects_invalid_subject() -> None:
    with pytest.raises(InvalidTokenError):
        parse_user_id({"sub": "not-a-uuid"})


def test_token_hash_is_stable_and_not_plain_token() -> None:
    token = "example-token"

    assert hash_token(token) == hash_token(token)
    assert hash_token(token) != token
    assert len(hash_token(token)) == 64


# ---------------------------------------------------------------------------
# hash_password — must produce $2a$ prefix (pgcrypto.crypt() compatibility)
# ---------------------------------------------------------------------------

def test_hash_password_uses_2a_prefix() -> None:
    """hash_password() must produce a $2a$ bcrypt hash.

    pgcrypto.crypt() (used in app_authenticate_user) cannot verify $2b$ hashes.
    Python's bcrypt defaults to $2b$; forcing $2a$ here is the fix.
    """
    h = hash_password("SomeSecurePass1!")
    assert h.startswith("$2a$"), (
        f"Expected $2a$ prefix so pgcrypto.crypt() can verify it, "
        f"but got {h[:4]!r}.  Check security.py:hash_password()."
    )


def test_hash_password_is_verifiable_by_python_bcrypt() -> None:
    pw = "VerifiablePass9#"
    h = hash_password(pw)
    assert bcrypt.checkpw(pw.encode(), h.encode())


def test_hash_password_generates_unique_salts() -> None:
    pw = "SamePassword1!"
    assert hash_password(pw) != hash_password(pw)


def test_verify_password_accepts_correct_password() -> None:
    password = b"test-password"
    stored_hash = bcrypt.hashpw(password, bcrypt.gensalt()).decode("utf-8")

    assert verify_password("test-password", stored_hash) is True


def test_verify_password_rejects_wrong_password() -> None:
    password_hash = bcrypt.hashpw(
        b"correct-password",
        bcrypt.gensalt(),
    ).decode("utf-8")

    assert verify_password("wrong-password", password_hash) is False


def test_verify_password_rejects_malformed_hash() -> None:
    assert verify_password("some-password", "not-a-valid-bcrypt-hash") is False
