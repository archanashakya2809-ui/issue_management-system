from datetime import UTC, datetime
import hmac

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RefreshResponse,
    UserMeResponse,
    UserRead,
)
from app.schemas.user import ProfileUpdate
from app.services.auth_service import (
    get_user_roles,
    login,
    logout_session,
    rotate_refresh_token,
)
from app.services.user_service import update_user_profile

router = APIRouter(prefix="/auth", tags=["authentication"])


def _set_refresh_cookies(
    response: Response,
    *,
    refresh_token: str,
    refresh_expires_at: datetime,
    csrf_token: str,
) -> None:
    max_age = max(0, int((refresh_expires_at - datetime.now(UTC)).total_seconds()))
    secure = settings.effective_refresh_cookie_secure
    same_site = settings.refresh_cookie_samesite

    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=max_age,
        path=settings.refresh_cookie_path,
        secure=secure,
        httponly=True,
        samesite=same_site,
    )
    # The CSRF cookie is intentionally readable by the frontend so it can be
    # echoed in X-CSRF-Token. It is not an authentication credential.
    response.set_cookie(
        key=settings.refresh_csrf_cookie_name,
        value=csrf_token,
        max_age=max_age,
        path=settings.refresh_cookie_path,
        secure=secure,
        httponly=False,
        samesite=same_site,
    )


def _clear_refresh_cookies(response: Response) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        secure=settings.effective_refresh_cookie_secure,
        httponly=True,
        samesite=settings.refresh_cookie_samesite,
    )
    response.delete_cookie(
        key=settings.refresh_csrf_cookie_name,
        path=settings.refresh_cookie_path,
        secure=settings.effective_refresh_cookie_secure,
        httponly=False,
        samesite=settings.refresh_cookie_samesite,
    )


def _validate_csrf_request(request: Request) -> str:
    cookie_value = request.cookies.get(settings.refresh_csrf_cookie_name)
    header_value = request.headers.get("x-csrf-token")
    if not cookie_value or not header_value or not hmac.compare_digest(cookie_value, header_value):
        raise HTTPException(status_code=403, detail="CSRF validation failed")

    origin = request.headers.get("origin")
    allowed_origins = {value.rstrip("/") for value in settings.cors_origin_list}
    if origin and origin.rstrip("/") not in allowed_origins:
        raise HTTPException(status_code=403, detail="Request origin is not allowed")
    if settings.app_env.lower() == "production" and not origin:
        raise HTTPException(status_code=403, detail="Origin header is required")
    return header_value


def _bearer_token(request: Request) -> str | None:
    value = request.headers.get("authorization", "")
    scheme, _, credentials = value.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        return None
    return credentials.strip()


@router.post("/login", response_model=LoginResponse)
def login_route(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
) -> LoginResponse:
    user, tokens = login(
        db,
        company_code=payload.company_code,
        email=str(payload.email),
        password=payload.password,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    _set_refresh_cookies(
        response,
        refresh_token=tokens.refresh_token,
        refresh_expires_at=tokens.refresh_expires_at,
        csrf_token=tokens.csrf_token,
    )
    return LoginResponse(
        access_token=tokens.access_token,
        expires_in_seconds=max(
            0, int((tokens.access_expires_at - datetime.now(UTC)).total_seconds())
        ),
        csrf_token=tokens.csrf_token,
        user=UserRead.model_validate(user),
    )


@router.post("/refresh", response_model=RefreshResponse)
def refresh_route(request: Request, response: Response, db: DbSession) -> RefreshResponse:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh session is missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    csrf_token = _validate_csrf_request(request)
    tokens = rotate_refresh_token(
        db,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    _set_refresh_cookies(
        response,
        refresh_token=tokens.refresh_token,
        refresh_expires_at=tokens.refresh_expires_at,
        csrf_token=tokens.csrf_token,
    )
    return RefreshResponse(
        access_token=tokens.access_token,
        expires_in_seconds=max(
            0, int((tokens.access_expires_at - datetime.now(UTC)).total_seconds())
        ),
        csrf_token=tokens.csrf_token,
    )


@router.get("/me", response_model=UserMeResponse)
def read_current_user(current_user: CurrentUser, db: DbSession) -> UserMeResponse:
    roles = get_user_roles(db, user_id=current_user.id, company_id=current_user.company_id)
    return UserMeResponse(
        id=current_user.id,
        company_id=current_user.company_id,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        email=current_user.email,
        department_id=current_user.department_id,
        status=current_user.status,
        roles=roles,
    )


@router.patch("/me", response_model=UserMeResponse)
def update_profile(payload: ProfileUpdate, current_user: CurrentUser, db: DbSession) -> UserMeResponse:
    updated = update_user_profile(db, current_user=current_user, payload=payload)
    roles = get_user_roles(db, user_id=updated.id, company_id=updated.company_id)
    return UserMeResponse(
        id=updated.id,
        company_id=updated.company_id,
        first_name=updated.first_name,
        last_name=updated.last_name,
        email=updated.email,
        department_id=updated.department_id,
        status=updated.status,
        roles=roles,
    )


@router.post("/logout", status_code=204)
def logout_route(request: Request, response: Response, db: DbSession) -> None:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    csrf_token = _validate_csrf_request(request) if refresh_token else None
    logout_session(
        db,
        access_token=_bearer_token(request),
        refresh_token=refresh_token,
        csrf_token=csrf_token,
    )
    _clear_refresh_cookies(response)
