
import logging
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.security import (
    decode_access_token,
    hash_token,
    parse_user_id,
)
from app.db.session import get_db
from app.models.user import User
from app.models.user_session import UserSession
from app.models.user_session_access_token import UserSessionAccessToken
from app.services.permission_service import user_has_permission


logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[Session, Depends(get_db)]


def unauthorized(detail: str) -> HTTPException:
    """Create a standard HTTP 401 response."""
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    db: DbSession,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> User:
    """
    Authenticate a request using:
    1. JWT signature and claims.
    2. Access-token database record.
    3. Active login session.
    4. Active user account.

    RLS identity is established before protected database queries.
    """

    # ---------------------------------------------------------
    # STEP 1: Check whether the Authorization header exists.
    # ---------------------------------------------------------

    if credentials is None:
        raise unauthorized("Authentication required")

    if credentials.scheme.lower() != "bearer":
        raise unauthorized("Bearer authentication required")

    token = credentials.credentials

    # ---------------------------------------------------------
    # STEP 2: Validate the JWT.
    # ---------------------------------------------------------

    try:
        claims = decode_access_token(token)

        user_id = parse_user_id(claims)

        if claims.get("token_use") != "access":
            raise InvalidTokenError("Invalid token purpose")

        company_id_claim = claims.get("company_id")

        if not isinstance(company_id_claim, str):
            raise InvalidTokenError("Missing company identity")

        token_company_id = UUID(company_id_claim)

    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        logger.warning("JWT validation failed: %s", type(exc).__name__)
        raise unauthorized("Invalid or expired access token") from exc

    # ---------------------------------------------------------
    # STEP 3: Establish the PostgreSQL RLS identity.
    # ---------------------------------------------------------

    # true = transaction-local setting.
    # It remains available for queries in this transaction.
    db.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(user_id)},
    )

    # ---------------------------------------------------------
    # STEP 4: Calculate the SHA-256 hash of the JWT.
    # ---------------------------------------------------------

    token_digest = hash_token(token)

    # ---------------------------------------------------------
    # STEP 5: Find the active access-token record and session.
    # ---------------------------------------------------------

    active_record = db.execute(
        select(UserSessionAccessToken, UserSession)
        .join(
            UserSession,
            (
                UserSession.id
                == UserSessionAccessToken.session_id
            )
            & (
                UserSession.user_id
                == UserSessionAccessToken.user_id
            )
            & (
                UserSession.company_id
                == UserSessionAccessToken.company_id
            ),
        )
        .where(
            # Access-token identity
            UserSessionAccessToken.user_id == user_id,
            UserSessionAccessToken.company_id == token_company_id,

            # The stored hash must match the presented JWT.
            UserSessionAccessToken.token_hash == token_digest,

            # Access token must be active.
            UserSessionAccessToken.revoked_at.is_(None),
            UserSessionAccessToken.expires_at > func.now(),

            # Parent session must also be active.
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > func.now(),
        )
        .limit(1)
    ).first()

    if active_record is None:
        logger.warning(
            "Access-token database validation failed for user_id=%s",
            user_id,
        )
        raise unauthorized(
            "Session or access token is no longer active"
        )

    access_token_row, active_session = active_record

    # ---------------------------------------------------------
    # STEP 6: Verify that the user account is active.
    # ---------------------------------------------------------

    user = db.scalar(
        select(User).where(
            User.id == user_id,
            User.company_id == active_session.company_id,
            User.status == "active",
            User.deleted_at.is_(None),
        )
    )

    if user is None:
        raise unauthorized("User is inactive or unavailable")

    # ---------------------------------------------------------
    # STEP 7: Return the authenticated user.
    # ---------------------------------------------------------

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permission(permission_code: str):
    """
    Check whether the authenticated user has a named permission.

    Organizational scope and row-level access must still be
    enforced by the backend and PostgreSQL RLS.
    """

    def permission_dependency(
        db: DbSession,
        current_user: CurrentUser,
    ) -> User:

        has_permission = user_has_permission(
            db,
            user_id=current_user.id,
            company_id=current_user.company_id,
            permission_code=permission_code,
        )

        if not has_permission:
            raise HTTPException(
                status_code=403,
                detail=f"Permission required: {permission_code}",
            )

        return current_user

    return permission_dependency
