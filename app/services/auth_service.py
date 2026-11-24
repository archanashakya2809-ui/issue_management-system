from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hmac
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_csrf_token,
    create_refresh_token,
    decode_access_token,
    hash_token,
    parse_user_id,
)
from app.models.company import Company
from app.models.user import User
from app.models.user_session import UserSession
from app.models.user_refresh_token import UserRefreshToken
from app.models.user_session_access_token import UserSessionAccessToken
from app.schemas.auth import RoleAssignmentRead
from app.services.audit_service import write_audit_event

# Fallback scope used when user_role_assignments.scope_type is NULL.
_ROLE_SCOPE: dict[str, str] = {
    "employee": "self",
    "support_agent": "team",
    "team_lead": "team",
    "department_manager": "department",
    "company_admin": "company",
    "system_admin": "system",
}


@dataclass(frozen=True)
class IssuedAuthTokens:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    csrf_token: str


def _set_user_context(db: Session, user_id: UUID) -> None:
    db.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(user_id)},
    )


def _set_refresh_lookup_context(db: Session, refresh_hash: str) -> None:
    # RLS exposes only the row whose hash is supplied by the backend for lookup.
    db.execute(
        text("SELECT set_config('app.refresh_token_hash', :token_hash, true)"),
        {"token_hash": refresh_hash},
    )


def _revoke_refresh_family(
    db: Session,
    *,
    family_id: UUID,
    user_id: UUID,
    session: UserSession | None,
    now: datetime,
) -> None:
    _set_user_context(db, user_id)
    db.execute(
        update(UserRefreshToken)
        .where(
            UserRefreshToken.family_id == family_id,
            UserRefreshToken.user_id == user_id,
            UserRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    if session is not None:
        was_active = session.revoked_at is None
        if was_active:
            session.revoked_at = now
        db.execute(
            update(UserSessionAccessToken)
            .where(
                UserSessionAccessToken.session_id == session.id,
                UserSessionAccessToken.user_id == user_id,
                UserSessionAccessToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        if was_active:
            write_audit_event(
                db,
                company_id=session.company_id,
                action="auth.refresh_family_revoked",
                entity_type="user",
                entity_id=user_id,
                metadata={"family_id": str(family_id)},
            )


def login(
    db: Session,
    *,
    company_code: str,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[User, IssuedAuthTokens]:
    # Verify credentials using the narrowly scoped database helper.
    auth_result = db.execute(
        text("""
            SELECT authenticated_user_id, authenticated_company_id
            FROM public.app_authenticate_user(
                :company_code,
                :email,
                :password
            )
        """),
        {
            "company_code": company_code,
            "email": email,
            "password": password,
        },
    ).mappings().first()

    if auth_result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    user_id = auth_result["authenticated_user_id"]
    company_id = auth_result["authenticated_company_id"]
    _set_user_context(db, user_id)

    result = db.execute(
        select(User, Company)
        .join(Company, Company.id == User.company_id)
        .where(
            User.id == user_id,
            User.company_id == company_id,
            User.status == "active",
            User.deleted_at.is_(None),
            Company.status == "active",
        )
    ).first()

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    user, company = result
    access_token, access_expires_at = create_access_token(user.id, user.company_id)
    refresh_token = create_refresh_token()
    csrf_token = create_csrf_token()
    now = datetime.now(UTC)
    refresh_expires_at = now + timedelta(days=settings.refresh_token_days)
    family_id = uuid4()

    session_row = UserSession(
        company_id=user.company_id,
        user_id=user.id,
        session_token_hash=hash_token(access_token),
        issued_at=now,
        expires_at=access_expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session_row)
    db.flush()

    db.add(
        UserSessionAccessToken(
            company_id=user.company_id,
            user_id=user.id,
            session_id=session_row.id,
            token_hash=hash_token(access_token),
            issued_at=now,
            expires_at=access_expires_at,
        )
    )
    db.add(
        UserRefreshToken(
            company_id=user.company_id,
            user_id=user.id,
            session_id=session_row.id,
            family_id=family_id,
            token_hash=hash_token(refresh_token),
            csrf_token_hash=hash_token(csrf_token),
            issued_at=now,
            expires_at=refresh_expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )

    write_audit_event(
        db,
        company_id=user.company_id,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        metadata={"company_code": company.code},
    )
    db.commit()

    return user, IssuedAuthTokens(
        access_token=access_token,
        access_expires_at=access_expires_at,
        refresh_token=refresh_token,
        refresh_expires_at=refresh_expires_at,
        csrf_token=csrf_token,
    )


def rotate_refresh_token(
    db: Session,
    *,
    refresh_token: str,
    csrf_token: str,
    ip_address: str | None,
    user_agent: str | None,
) -> IssuedAuthTokens:
    """Rotate one opaque refresh token; reuse revokes the complete token family."""
    now = datetime.now(UTC)
    presented_hash = hash_token(refresh_token)
    _set_refresh_lookup_context(db, presented_hash)

    current = db.scalar(
        select(UserRefreshToken)
        .where(UserRefreshToken.token_hash == presented_hash)
        .with_for_update()
    )
    if current is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh session is invalid or no longer active",
        )

    if not hmac.compare_digest(current.csrf_token_hash, hash_token(csrf_token)):
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")

    user_id = current.user_id
    _set_user_context(db, user_id)
    session_row = db.scalar(
        select(UserSession)
        .where(UserSession.id == current.session_id, UserSession.user_id == user_id)
        .with_for_update()
    )

    # Reusing any already-consumed token is treated as credential theft. Revoke
    # the family and the associated access-token session in the same transaction.
    if current.used_at is not None:
        _revoke_refresh_family(
            db,
            family_id=current.family_id,
            user_id=user_id,
            session=session_row,
            now=now,
        )
        write_audit_event(
            db,
            company_id=current.company_id,
            action="auth.refresh_token_reuse_detected",
            entity_type="user",
            entity_id=user_id,
            metadata={"family_id": str(current.family_id)},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected. Please sign in again.",
        )

    if current.revoked_at is not None or current.expires_at <= now:
        _revoke_refresh_family(
            db,
            family_id=current.family_id,
            user_id=user_id,
            session=session_row,
            now=now,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh session has expired or been revoked",
        )

    if session_row is None or session_row.revoked_at is not None:
        _revoke_refresh_family(
            db,
            family_id=current.family_id,
            user_id=user_id,
            session=session_row,
            now=now,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh session is no longer active",
        )

    user_company = db.execute(
        select(User, Company)
        .join(Company, Company.id == User.company_id)
        .where(
            User.id == current.user_id,
            User.company_id == current.company_id,
            User.status == "active",
            User.deleted_at.is_(None),
            Company.status == "active",
        )
    ).first()
    if user_company is None:
        _revoke_refresh_family(
            db,
            family_id=current.family_id,
            user_id=user_id,
            session=session_row,
            now=now,
        )
        write_audit_event(
            db,
            company_id=current.company_id,
            action="auth.refresh_denied_inactive_user",
            entity_type="user",
            entity_id=user_id,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is inactive or unavailable",
        )

    new_access_token, access_expires_at = create_access_token(user_id, current.company_id)
    new_refresh_token = create_refresh_token()

    # Keep the refresh lifetime absolute from initial login. Rotation does not
    # extend a stolen session indefinitely.
    current.used_at = now
    db.add(
        UserSessionAccessToken(
            company_id=current.company_id,
            user_id=user_id,
            session_id=current.session_id,
            token_hash=hash_token(new_access_token),
            issued_at=now,
            expires_at=access_expires_at,
        )
    )
    new_row = UserRefreshToken(
        company_id=current.company_id,
        user_id=user_id,
        session_id=current.session_id,
        family_id=current.family_id,
        token_hash=hash_token(new_refresh_token),
        csrf_token_hash=current.csrf_token_hash,
        parent_token_id=current.id,
        issued_at=now,
        expires_at=current.expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(new_row)
    db.flush()
    current.replaced_by_token_id = new_row.id

    # Keep the legacy digest synchronized for a controlled application rollback.
    # All unexpired, non-revoked token records remain independently valid.
    session_row.session_token_hash = hash_token(new_access_token)
    session_row.expires_at = access_expires_at

    write_audit_event(
        db,
        company_id=current.company_id,
        action="auth.refresh",
        entity_type="user",
        entity_id=user_id,
        metadata={"family_id": str(current.family_id)},
    )
    refresh_expires_at = current.expires_at
    db.commit()

    return IssuedAuthTokens(
        access_token=new_access_token,
        access_expires_at=access_expires_at,
        refresh_token=new_refresh_token,
        refresh_expires_at=refresh_expires_at,
        csrf_token=csrf_token,
    )


def logout_session(
    db: Session,
    *,
    access_token: str | None,
    refresh_token: str | None,
    csrf_token: str | None,
) -> None:
    """Revoke the refresh family and access session when browser credentials exist."""
    now = datetime.now(UTC)

    if refresh_token:
        refresh_hash = hash_token(refresh_token)
        _set_refresh_lookup_context(db, refresh_hash)
        refresh_row = db.scalar(
            select(UserRefreshToken)
            .where(UserRefreshToken.token_hash == refresh_hash)
            .with_for_update()
        )
        if refresh_row is not None:
            if not csrf_token or not hmac.compare_digest(
                refresh_row.csrf_token_hash, hash_token(csrf_token)
            ):
                db.rollback()
                raise HTTPException(status_code=403, detail="Invalid CSRF token")

            _set_user_context(db, refresh_row.user_id)
            session_row = db.scalar(
                select(UserSession)
                .where(UserSession.id == refresh_row.session_id)
                .with_for_update()
            )
            _revoke_refresh_family(
                db,
                family_id=refresh_row.family_id,
                user_id=refresh_row.user_id,
                session=session_row,
                now=now,
            )
            write_audit_event(
                db,
                company_id=refresh_row.company_id,
                action="auth.logout",
                entity_type="user",
                entity_id=refresh_row.user_id,
            )

    # If the refresh cookie is absent/unknown, a still-valid bearer token can
    # independently identify and revoke its access session.
    if access_token:
        try:
            claims = decode_access_token(access_token)
            access_user_id = parse_user_id(claims)
        except Exception:
            access_user_id = None

        if access_user_id is not None:
            _set_user_context(db, access_user_id)
            access_token_row = db.scalar(
                select(UserSessionAccessToken).where(
                    UserSessionAccessToken.user_id == access_user_id,
                    UserSessionAccessToken.token_hash == hash_token(access_token),
                )
            )
            access_session = None
            if access_token_row is not None:
                access_session = db.scalar(
                    select(UserSession)
                    .where(
                        UserSession.id == access_token_row.session_id,
                        UserSession.user_id == access_user_id,
                    )
                    .with_for_update()
                )
            if access_session is not None:
                if access_session.revoked_at is None:
                    access_session.revoked_at = now
                db.execute(
                    update(UserSessionAccessToken)
                    .where(
                        UserSessionAccessToken.session_id == access_session.id,
                        UserSessionAccessToken.user_id == access_user_id,
                        UserSessionAccessToken.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
                db.execute(
                    update(UserRefreshToken)
                    .where(
                        UserRefreshToken.session_id == access_session.id,
                        UserRefreshToken.user_id == access_user_id,
                        UserRefreshToken.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
                write_audit_event(
                    db,
                    company_id=access_session.company_id,
                    action="auth.logout",
                    entity_type="user",
                    entity_id=access_user_id,
                )

    db.commit()


def get_user_roles(
    db: Session,
    *,
    user_id: UUID,
    company_id: UUID,
) -> list[RoleAssignmentRead]:
    """Return the caller's active role assignments for the /auth/me response."""
    rows = db.execute(
        text("""
            SELECT lower(r.code)        AS role_code,
                   ura.company_id       AS company_id,
                   ura.scope_type       AS scope_type,
                   ura.scope_department_id AS department_id,
                   ura.scope_team_id    AS team_id
            FROM public.user_role_assignments ura
            JOIN public.roles r ON r.id = ura.role_id
            WHERE ura.user_id    = :user_id
              AND ura.company_id  = :company_id
              AND ura.revoked_at  IS NULL
              AND r.is_active     = TRUE
            ORDER BY r.code ASC
        """),
        {"user_id": user_id, "company_id": company_id},
    ).mappings().all()

    result: list[RoleAssignmentRead] = []
    for row in rows:
        code = str(row["role_code"])
        scope = str(row["scope_type"]) if row["scope_type"] else _ROLE_SCOPE.get(code, "self")
        result.append(RoleAssignmentRead(
            role_code=code,
            scope=scope,
            company_id=row["company_id"],
            department_id=row["department_id"],
            team_id=row["team_id"],
        ))
    return result
