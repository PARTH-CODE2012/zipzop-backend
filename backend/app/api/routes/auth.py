"""Registration, sessions and /me — contract §2.

**Where the refresh token lives.** Contract 1.1 carried it in the request and
response body. It is now an httpOnly cookie: JavaScript cannot read it, so an
XSS that can call the API on the user's behalf still cannot walk away with a
30-day credential. The access token stays in the response body and in memory,
where a 15-minute lifetime makes the exposure small.

The cost is that a non-browser client cannot hold a session. Phase 1 is web
only, so that is not a cost yet; when the mobile app arrives it needs a second
grant type, not a change to this one.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response, status

from app.api import ids
from app.api.deps import CurrentUser, Session, auth_rate_limit, general_rate_limit
from app.api.errors import InvalidCredentialsError, TokenExpiredError, TokenRevokedError
from app.api.schemas.auth import (
    CreditBalances,
    LoginRequest,
    MeResponse,
    PlanLimits,
    RefreshResponse,
    RegisterRequest,
    SessionResponse,
    SubscriptionSummary,
    UserSummary,
)
from app.config import settings
from app.logging import get_logger
from app.models import User
from app.repositories.user import RefreshTokenRepository, UserRepository
from app.services import security
from app.services.plans import concurrency_for

log = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

#: Scoped to the only two endpoints that read it. A cookie sent on every
#: request to every path is a cookie that will eventually be logged by
#: something.
REFRESH_COOKIE_NAME = "zipzop_refresh"
REFRESH_COOKIE_PATH = "/v1/auth"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        # `secure` off in local development only — the dev frontend is served
        # over plain http and a Secure cookie would simply never be sent, which
        # looks exactly like a broken login.
        secure=settings.environment not in ("local", "test"),
        # `lax` and not `strict`: the user must stay signed in when they arrive
        # from a checkout redirect (M6). Not `none`, which would allow any site
        # to trigger a refresh.
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)


def _session_payload(user: User, access_token: str, expires_in: int) -> SessionResponse:
    return SessionResponse(
        user=UserSummary(
            id=ids.encode(ids.USER, user.id),
            email=user.email,
            display_name=user.display_name,
        ),
        access_token=access_token,
        expires_in=expires_in,
    )


async def _start_session(
    request: Request, response: Response, session: Session, user: User
) -> SessionResponse:
    access_token, expires_in = security.issue_access_token(user.id)
    refresh_token, digest = security.new_refresh_token()
    await RefreshTokenRepository(session).issue(
        user_id=user.id,
        token_hash=digest,
        expires_at=security.refresh_token_expiry(),
        user_agent=request.headers.get("user-agent"),
    )
    _set_refresh_cookie(response, refresh_token)
    return _session_payload(user, access_token, expires_in)


# --------------------------------------------------------------------------


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionResponse,
    dependencies=[Depends(auth_rate_limit)],
    summary="Create an account",
)
async def register(
    body: RegisterRequest, request: Request, response: Response, session: Session
) -> SessionResponse:
    users = UserRepository(session)

    if await users.email_exists(body.email):
        # Registration cannot hide that an address is taken — the user has to
        # be told. Login is where the enumeration defence lives.
        raise InvalidCredentialsError(
            "An account already exists for that email address.",
            details={"field": "email"},
        )

    user = await users.create_with_free_plan(
        email=body.email,
        hashed_password=security.hash_password(body.password),
        display_name=body.display_name,
    )
    log.info("registered", user_id=str(user.id))
    return await _start_session(request, response, session, user)


@router.post(
    "/login",
    response_model=SessionResponse,
    dependencies=[Depends(auth_rate_limit)],
    summary="Sign in",
)
async def login(
    body: LoginRequest, request: Request, response: Response, session: Session
) -> SessionResponse:
    user = await UserRepository(session).by_email(body.email)

    if user is None:
        # Verify against a fixed hash anyway. Returning early here would make
        # "no such account" answer in a millisecond and "wrong password" in
        # eighty, which tells an attacker which addresses are registered just
        # as clearly as a different error message would.
        security.dummy_verify()
        raise InvalidCredentialsError()

    if not security.verify_password(body.password, user.hashed_password):
        raise InvalidCredentialsError()

    return await _start_session(request, response, session, user)


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    dependencies=[Depends(auth_rate_limit)],
    summary="Exchange the refresh cookie for a new access token",
)
async def refresh(
    request: Request,
    response: Response,
    session: Session,
    zipzop_refresh: Annotated[str | None, Cookie()] = None,
) -> RefreshResponse:
    if not zipzop_refresh:
        raise TokenRevokedError("No session cookie was sent.")

    tokens = RefreshTokenRepository(session)
    stored = await tokens.by_hash(security.hash_refresh_token(zipzop_refresh))

    if stored is None:
        raise TokenRevokedError()

    if stored.replaced_by is not None:
        # A token that was already rotated is being presented again. Either the
        # legitimate client lost the response and retried, or a copy leaked —
        # and those are indistinguishable from here. Revoke the whole chain and
        # make everyone sign in again; a false positive costs one login, a
        # false negative costs the account.
        ended = await tokens.revoke_all_for_user(stored.user_id)
        log.warning("refresh_token_reuse", user_id=str(stored.user_id), sessions_ended=ended)
        # **Committed before raising, and it has to be.** `get_session` rolls
        # back on any exception, so without this the 401 below would undo the
        # revocation this branch exists to perform: the API would log the reuse,
        # tell the user to sign in again, and leave every token in the chain
        # valid — surviving exactly the event that is meant to kill it.
        await session.commit()
        _clear_refresh_cookie(response)
        raise TokenRevokedError("This session was ended for security. Please sign in again.")

    if stored.revoked_at is not None:
        _clear_refresh_cookie(response)
        raise TokenRevokedError()

    if stored.expires_at <= datetime.now(UTC):
        _clear_refresh_cookie(response)
        raise TokenExpiredError("Your session has expired. Please sign in again.")

    user = await UserRepository(session).by_id(stored.user_id)
    if user is None:
        raise TokenRevokedError()

    fresh_token, digest = security.new_refresh_token()
    await tokens.rotate(
        old=stored,
        token_hash=digest,
        expires_at=security.refresh_token_expiry(),
        user_agent=request.headers.get("user-agent"),
    )
    _set_refresh_cookie(response, fresh_token)

    access_token, expires_in = security.issue_access_token(user.id)
    return RefreshResponse(access_token=access_token, expires_in=expires_in)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(auth_rate_limit)],
    summary="End this session",
)
async def logout(
    response: Response,
    session: Session,
    zipzop_refresh: Annotated[str | None, Cookie()] = None,
) -> Response:
    if zipzop_refresh:
        tokens = RefreshTokenRepository(session)
        stored = await tokens.by_hash(security.hash_refresh_token(zipzop_refresh))
        if stored is not None and stored.revoked_at is None:
            await tokens.revoke(stored)

    # Always 204, whatever was presented. Logout that reports "there was no
    # session" is an oracle, and there is nothing useful the client could do
    # with the distinction anyway.
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(out)
    return out


# `/me` sits at the top level, not under `/auth` — contract §2 and §10 list it
# as its own endpoint, and it is about the account rather than the session.
me_router = APIRouter(tags=["auth"])


@me_router.get(
    "/me",
    response_model=MeResponse,
    dependencies=[Depends(general_rate_limit)],
    summary="The signed-in account, its balances and its plan limits",
)
async def me(user: CurrentUser, session: Session) -> MeResponse:
    users = UserRepository(session)
    subscription = await users.live_subscription(user.id)

    summary: SubscriptionSummary | None = None
    expires_at = None
    if subscription is not None:
        plan = await users.plan(subscription.plan)
        assert plan is not None  # FK guarantees it
        expires_at = subscription.current_period_end
        summary = SubscriptionSummary(
            plan=subscription.plan.value,
            display_name=plan.display_name,
            status=subscription.status.value,
            currency=subscription.currency,
            current_period_end=subscription.current_period_end,
            cancel_at_period_end=subscription.cancel_at_period_end,
            provider=subscription.provider.value if subscription.provider else None,
            limits=PlanLimits(
                max_export_height=plan.max_export_height,
                watermark=plan.watermark.value,
                monthly_credits=plan.monthly_credits,
                facemap_seconds=plan.facemap_seconds,
                concurrent_jobs=concurrency_for(plan.code),
            ),
        )

    return MeResponse(
        id=ids.encode(ids.USER, user.id),
        email=user.email,
        display_name=user.display_name,
        storage_bytes_used=user.storage_bytes_used,
        created_at=user.created_at,
        credits=CreditBalances(
            plan=user.plan_credits,
            topup=user.topup_credits,
            total=user.total_credits,
            facemap_seconds=user.facemap_seconds,
            # Plan credits expire when the period rolls over; topup never does.
            plan_credits_expire_at=expires_at,
        ),
        subscription=summary,
    )
