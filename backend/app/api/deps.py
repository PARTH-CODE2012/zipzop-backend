"""Request dependencies: who is calling, and may they call this often.

Every product route depends on `CurrentUser`. There is no "optional auth" here
— an endpoint either needs an account or it does not, and mixing the two in one
handler is how a route ends up returning somebody else's rows to an anonymous
caller.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ForbiddenError, RateLimitedError, TokenRevokedError
from app.config import settings
from app.db import get_session
from app.models import User, UserStatus
from app.repositories.user import UserRepository
from app.services import rate_limit
from app.services.security import read_access_token

Session = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str:
    """The address rate limits are counted against.

    **Read from the right of `X-Forwarded-For`, never the left.** A proxy
    appends the address it saw to whatever chain arrived, so with
    `trusted_proxy_hops = N` the entry N places from the right is the one our
    own outermost proxy wrote, and everything to its left is client-supplied.

    This used to take the *first* entry — precisely the one the client writes.
    Found in M7 (docs/07-security.md §6.10): sending a different made-up address
    on every request gave each one a fresh allowance, so the 20-a-minute
    ceiling on `/auth/login` stopped nothing. A chain shorter than the trusted
    hop count was not built by our proxies, so none of it is believed and the
    socket peer is used instead.

    Still only ever for throttling, never for authorisation: a misconfigured
    hop count makes it wrong, and wrong must only ever cost a rate limit.
    """
    peer = request.client.host if request.client else "unknown"
    hops = settings.trusted_proxy_hops
    if hops <= 0:
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    chain = [part.strip() for part in forwarded.split(",") if part.strip()]
    if len(chain) < hops:
        return peer
    return chain[-hops]


async def current_user(request: Request, session: Session) -> User:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise TokenRevokedError("This endpoint needs a signed-in account.")

    user_id: uuid.UUID = read_access_token(token)
    user = await UserRepository(session).by_id(user_id)
    if user is None:
        # The token verifies but names nobody: the account was deleted while a
        # token was still live.
        raise TokenRevokedError()
    if user.status is not UserStatus.ACTIVE:
        raise ForbiddenError("This account is not active.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def general_rate_limit(request: Request) -> None:
    """100 requests a minute per IP (docs/03-backend-architecture.md §10)."""
    await _enforce(request, bucket="general", limit=100, window_seconds=60)


async def auth_rate_limit(request: Request) -> None:
    """20 a minute on the auth routes.

    Tighter because these are the endpoints worth brute-forcing, and because a
    legitimate client hits them once per session rather than once per action.
    """
    await _enforce(request, bucket="auth", limit=20, window_seconds=60)


async def _enforce(request: Request, *, bucket: str, limit: int, window_seconds: int) -> None:
    ip = client_ip(request)
    verdict = await rate_limit.hit(f"{bucket}:{ip}", limit=limit, window_seconds=window_seconds)
    if not verdict.allowed:
        # Contract §1: 429 carries `Retry-After` in seconds. Also in `details`,
        # because a fetch() caller reads the body far more readily than it
        # reads a response header.
        raise RateLimitedError(
            details={"retryAfter": verdict.retry_after_seconds},
            headers={"Retry-After": str(verdict.retry_after_seconds)},
        )
