import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt
import jwt
from jwt import InvalidTokenError

from app.core.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "at+jwt"


def hash_token(token: str) -> str:
    """Store only a SHA-256 digest of a bearer token in PostgreSQL."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """Verify bcrypt-compatible hashes, including PostgreSQL crypt(..., gen_salt('bf'))."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), stored_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_password(plain: str) -> str:
    """Hash a password using the $2a$ prefix required by this project's pgcrypto verifier."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(prefix=b"2a")).decode("utf-8")


def create_refresh_token() -> str:
    """Create a high-entropy opaque refresh credential (never a JWT)."""
    return secrets.token_urlsafe(64)


def create_csrf_token() -> str:
    """Create a random double-submit CSRF value bound to a refresh-token family."""
    return secrets.token_urlsafe(32)


def create_access_token(user_id: UUID, company_id: UUID) -> tuple[str, datetime]:
    """Issue a short-lived, audience-bound access JWT using the active signing key."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": str(user_id),
        "company_id": str(company_id),
        "iss": settings.access_token_issuer,
        "aud": settings.access_token_audience,
        "iat": now,
        "nbf": now,
        "exp": expires_at,
        "jti": secrets.token_urlsafe(24),
        "token_use": "access",
    }
    token = jwt.encode(
        payload,
        settings.effective_access_token_signing_key,
        algorithm=ALGORITHM,
        headers={"kid": settings.access_token_kid, "typ": ACCESS_TOKEN_TYPE},
    )
    return token, expires_at


def decode_access_token(token: str) -> dict:
    """Validate signature, key ID, issuer, audience, lifetime and access-token type."""
    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError:
        raise

    # Never accept algorithm negotiation from the untrusted JWT header.
    if header.get("alg") != ALGORITHM:
        raise InvalidTokenError("Unexpected signing algorithm")
    if header.get("typ") != ACCESS_TOKEN_TYPE:
        raise InvalidTokenError("Unexpected token type")

    key_id = header.get("kid")
    if not isinstance(key_id, str) or not key_id:
        raise InvalidTokenError("Missing signing key ID")

    if key_id == settings.access_token_kid:
        signing_key = settings.effective_access_token_signing_key
    else:
        signing_key = settings.access_token_previous_signing_keys.get(key_id)
        if signing_key is None:
            raise InvalidTokenError("Unknown signing key ID")

    claims = jwt.decode(
        token,
        signing_key,
        algorithms=[ALGORITHM],
        issuer=settings.access_token_issuer,
        audience=settings.access_token_audience,
        options={
            "require": [
                "sub", "company_id", "iss", "aud", "iat", "nbf", "exp", "jti", "token_use"
            ]
        },
    )

    if claims.get("token_use") != "access":
        raise InvalidTokenError("Token is not an access token")
    if not isinstance(claims.get("jti"), str) or not claims["jti"]:
        raise InvalidTokenError("Invalid token identifier")

    try:
        UUID(claims["company_id"])
    except (ValueError, TypeError, KeyError) as exc:
        raise InvalidTokenError("Invalid company_id claim") from exc

    return claims


def parse_user_id(claims: dict) -> UUID:
    try:
        return UUID(claims["sub"])
    except (ValueError, TypeError, KeyError) as exc:
        raise InvalidTokenError("Invalid subject claim") from exc
