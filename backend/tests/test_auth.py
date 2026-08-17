"""Auth, against the real database — contract §2.

The tests worth having here are the ones about what the endpoints *refuse* to
do: leak which addresses are registered, hand a readable refresh token to
JavaScript, or let a rotated token be used twice.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import REFRESH_COOKIE_NAME
from app.models import (
    CreditBucket,
    CreditLedgerEntry,
    LedgerReason,
    PlanCode,
    RefreshToken,
    Subscription,
    SubStatus,
    User,
)
from app.services import security

V1 = "/v1"


def _email() -> str:
    return f"{uuid.uuid4().hex[:12]}@example.com"


async def _register(
    client: AsyncClient, email: str | None = None, password: str = "hunter2hunter2"
):
    return await client.post(
        f"{V1}/auth/register",
        json={"email": email or _email(), "password": password, "displayName": "Sam"},
    )


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


async def test_register_returns_a_session_and_a_prefixed_id(client: AsyncClient) -> None:
    response = await _register(client)
    assert response.status_code == 201

    body = response.json()
    assert body["user"]["id"].startswith("usr_")
    assert body["user"]["displayName"] == "Sam"
    assert body["expiresIn"] == 900
    assert body["accessToken"]


async def test_register_never_puts_the_refresh_token_in_the_body(client: AsyncClient) -> None:
    """The whole point of moving it to an httpOnly cookie.

    Returning it in the body as well would let any script read it, which is
    exactly the exposure the cookie exists to remove.
    """
    body = (await _register(client)).json()
    assert "refreshToken" not in body
    assert "refresh_token" not in body


async def test_register_sets_an_httponly_refresh_cookie(client: AsyncClient) -> None:
    response = await _register(client)
    header = response.headers.get("set-cookie", "")
    assert REFRESH_COOKIE_NAME in header
    assert "HttpOnly" in header
    assert "Path=/v1/auth" in header
    assert "SameSite=lax" in header.replace("Samesite", "SameSite")


async def test_register_grants_the_free_plan_in_one_transaction(
    client: AsyncClient, db: AsyncSession
) -> None:
    """contract §2: the allowance is already granted when register returns.

    Three things have to be true together — the balance, the ledger row that
    explains it, and the subscription. A balance with no ledger row is the
    accounting bug the nightly reconciliation exists to find, so it is worth
    asserting all three rather than just the number the client sees.
    """
    email = _email()
    await _register(client, email)

    user = (await db.execute(sa.select(User).where(User.email == email))).scalar_one()
    assert user.plan_credits == 300  # free plan, docs/03 §5.5
    assert user.topup_credits == 0
    assert user.facemap_seconds == 0

    entries = (
        (await db.execute(sa.select(CreditLedgerEntry).where(CreditLedgerEntry.user_id == user.id)))
        .scalars()
        .all()
    )
    assert len(entries) == 1
    assert entries[0].reason is LedgerReason.SIGNUP_GRANT
    assert entries[0].bucket is CreditBucket.PLAN
    assert entries[0].delta == 300
    assert entries[0].balance_after == 300

    sub = (
        await db.execute(sa.select(Subscription).where(Subscription.user_id == user.id))
    ).scalar_one()
    assert sub.plan is PlanCode.FREE
    assert sub.status is SubStatus.ACTIVE
    assert sub.provider is None


async def test_free_plan_grants_no_facemap_ledger_row(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The free tier includes 0 facemap seconds, and the ledger forbids a zero
    delta. Writing the row unconditionally would make every registration fail
    on a constraint — so exactly one row, for the bucket that moved."""
    email = _email()
    assert (await _register(client, email)).status_code == 201

    user = (await db.execute(sa.select(User).where(User.email == email))).scalar_one()
    facemap_rows = await db.scalar(
        sa.select(sa.func.count())
        .select_from(CreditLedgerEntry)
        .where(
            CreditLedgerEntry.user_id == user.id,
            CreditLedgerEntry.bucket == CreditBucket.FACEMAP,
        )
    )
    assert facemap_rows == 0


async def test_a_taken_email_is_rejected(client: AsyncClient) -> None:
    email = _email()
    assert (await _register(client, email)).status_code == 201
    second = await _register(client, email)
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_a_passphrase_longer_than_bcrypt_accepts_still_works(client: AsyncClient) -> None:
    """bcrypt takes 72 bytes. Passwords are SHA-256'd first, so a long
    passphrase neither errors nor gets silently truncated — and two passphrases
    sharing their first 72 bytes remain different passwords."""
    email = _email()
    base = "correct horse battery staple " * 4  # 116 characters
    assert (await _register(client, email, password=base + "ending-one")).status_code == 201

    wrong = await client.post(
        f"{V1}/auth/login", json={"email": email, "password": base + "ending-two"}
    )
    assert wrong.status_code == 401

    right = await client.post(
        f"{V1}/auth/login", json={"email": email, "password": base + "ending-one"}
    )
    assert right.status_code == 200


async def test_a_short_password_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        f"{V1}/auth/register", json={"email": _email(), "password": "short"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------


async def test_login_succeeds_and_rotates_in_a_new_session(client: AsyncClient) -> None:
    email = _email()
    await _register(client, email)
    response = await client.post(
        f"{V1}/auth/login", json={"email": email, "password": "hunter2hunter2"}
    )
    assert response.status_code == 200
    assert response.json()["user"]["email"] == email


async def test_wrong_password_and_unknown_email_are_indistinguishable(
    client: AsyncClient,
) -> None:
    """contract §2: *"identical for a wrong password and an unknown email, so
    the endpoint cannot be used to discover which addresses are registered."*

    Identical status, identical code, identical message — a difference in any
    one of the three is enough to enumerate accounts.
    """
    email = _email()
    await _register(client, email)

    wrong_password = await client.post(
        f"{V1}/auth/login", json={"email": email, "password": "not-the-password"}
    )
    unknown_email = await client.post(
        f"{V1}/auth/login", json={"email": _email(), "password": "not-the-password"}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_email_is_case_insensitive_at_login(client: AsyncClient) -> None:
    """CITEXT. Someone who registered as Sam@ must be able to sign in as sam@."""
    email = f"Mixed.Case.{uuid.uuid4().hex[:8]}@Example.COM"
    await _register(client, email)
    response = await client.post(
        f"{V1}/auth/login", json={"email": email.lower(), "password": "hunter2hunter2"}
    )
    assert response.status_code == 200


# --------------------------------------------------------------------------
# Refresh rotation
# --------------------------------------------------------------------------


async def test_refresh_issues_a_new_access_token_and_rotates_the_cookie(
    client: AsyncClient,
) -> None:
    await _register(client)
    first_cookie = client.cookies.get(REFRESH_COOKIE_NAME)
    assert first_cookie

    response = await client.post(f"{V1}/auth/refresh")
    assert response.status_code == 200
    assert response.json()["accessToken"]

    second_cookie = client.cookies.get(REFRESH_COOKIE_NAME)
    assert second_cookie and second_cookie != first_cookie


async def test_replaying_a_rotated_token_revokes_the_whole_chain(
    client: AsyncClient, db: AsyncSession
) -> None:
    """*"Presenting an already-rotated token revokes the whole chain and forces
    a fresh sign-in; that pattern means a token leaked."*

    The test that matters most in this file: it is the difference between a
    stolen refresh token being good for thirty days and being good until the
    real user next refreshes.
    """
    email = _email()
    await _register(client, email)
    stolen = client.cookies.get(REFRESH_COOKIE_NAME)
    assert stolen

    # The legitimate client refreshes, rotating `stolen` out.
    assert (await client.post(f"{V1}/auth/refresh")).status_code == 200
    live = client.cookies.get(REFRESH_COOKIE_NAME)

    # The thief presents the old one.
    client.cookies.set(REFRESH_COOKIE_NAME, stolen)
    replay = await client.post(f"{V1}/auth/refresh")
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "TOKEN_REVOKED"

    # And the legitimate session is dead too — that is the point.
    client.cookies.set(REFRESH_COOKIE_NAME, str(live))
    after = await client.post(f"{V1}/auth/refresh")
    assert after.status_code == 401

    user = (await db.execute(sa.select(User).where(User.email == email))).scalar_one()
    live_tokens = await db.scalar(
        sa.select(sa.func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
    )
    assert live_tokens == 0


async def test_refresh_without_a_cookie_is_refused(client: AsyncClient) -> None:
    response = await client.post(f"{V1}/auth/refresh")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_REVOKED"


async def test_an_expired_refresh_token_is_refused(client: AsyncClient, db: AsyncSession) -> None:
    await _register(client)
    token = client.cookies.get(REFRESH_COOKIE_NAME)
    assert token

    await db.execute(
        sa.update(RefreshToken)
        .where(RefreshToken.token_hash == security.hash_refresh_token(token))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.flush()

    response = await client.post(f"{V1}/auth/refresh")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


# --------------------------------------------------------------------------
# Logout
# --------------------------------------------------------------------------


async def test_logout_revokes_the_session(client: AsyncClient) -> None:
    await _register(client)
    assert (await client.post(f"{V1}/auth/logout")).status_code == 204
    assert (await client.post(f"{V1}/auth/refresh")).status_code == 401


async def test_logout_without_a_session_still_returns_204(client: AsyncClient) -> None:
    """No oracle: logout must not report whether there was anything to end."""
    assert (await client.post(f"{V1}/auth/logout")).status_code == 204


# --------------------------------------------------------------------------
# /me
# --------------------------------------------------------------------------


async def _bearer(client: AsyncClient) -> dict[str, str]:
    body = (await _register(client)).json()
    return {"Authorization": f"Bearer {body['accessToken']}"}


async def test_me_needs_a_token(client: AsyncClient) -> None:
    assert (await client.get(f"{V1}/me")).status_code == 401


async def test_me_reports_three_balances_and_the_plan_limits(client: AsyncClient) -> None:
    response = await client.get(f"{V1}/me", headers=await _bearer(client))
    assert response.status_code == 200
    body = response.json()

    assert body["credits"] == {
        "plan": 300,
        "topup": 0,
        "total": 300,
        "facemapSeconds": 0,
        "planCreditsExpireAt": body["credits"]["planCreditsExpireAt"],
    }
    assert body["credits"]["planCreditsExpireAt"] is not None

    limits = body["subscription"]["limits"]
    assert limits["maxExportHeight"] == 720
    assert limits["watermark"] == "forced"
    assert limits["monthlyCredits"] == 300
    # docs/03 §5.3 — free gets one analysis slot and no inference at all.
    assert limits["concurrentJobs"] == {"analysis": 1, "render": 1, "inference": 0}


async def test_me_totals_exclude_the_facemap_meter(client: AsyncClient, db: AsyncSession) -> None:
    """facemapSeconds is a separate meter; only face mapping and lip sync spend
    it. Folding it into `total` would show a spendable balance that is not."""
    headers = await _bearer(client)
    me = (await client.get(f"{V1}/me", headers=headers)).json()
    user_id = uuid.UUID(me["id"].removeprefix("usr_"))

    await db.execute(
        sa.update(User).where(User.id == user_id).values(topup_credits=50, facemap_seconds=240)
    )
    await db.flush()

    credits = (await client.get(f"{V1}/me", headers=headers)).json()["credits"]
    assert credits == {
        "plan": 300,
        "topup": 50,
        "total": 350,
        "facemapSeconds": 240,
        "planCreditsExpireAt": credits["planCreditsExpireAt"],
    }


async def test_field_names_are_camel_case(client: AsyncClient) -> None:
    """contract §1. snake_case leaking out of the serialisation layer breaks
    every generated client type."""
    body = (await client.get(f"{V1}/me", headers=await _bearer(client))).json()
    assert "storageBytesUsed" in body
    assert "storage_bytes_used" not in body
    assert "displayName" in body


# --------------------------------------------------------------------------
# Access tokens
# --------------------------------------------------------------------------


async def test_an_expired_access_token_says_so_specifically(client: AsyncClient) -> None:
    """TOKEN_EXPIRED is the only 401 the client answers by refreshing rather
    than by signing the user out (contract §1), so it must not be flattened
    into a generic failure."""
    import jwt

    from app.config import settings

    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "typ": "access",
            "iat": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    response = await client.get(f"{V1}/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_a_refresh_token_is_not_a_bearer_credential(client: AsyncClient) -> None:
    await _register(client)
    refresh_token = client.cookies.get(REFRESH_COOKIE_NAME)
    response = await client.get(f"{V1}/me", headers={"Authorization": f"Bearer {refresh_token}"})
    assert response.status_code == 401


async def test_a_token_signed_with_another_key_is_refused(client: AsyncClient) -> None:
    import jwt

    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "typ": "access",
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        },
        # 32+ bytes, so the assertion is about the *wrong key* and not about
        # PyJWT refusing a short one.
        "a-different-key-of-a-perfectly-respectable-length",
        algorithm="HS256",
    )
    response = await client.get(f"{V1}/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/auth/login", "/auth/register"])
async def test_auth_endpoints_are_limited_to_twenty_a_minute(
    client: AsyncClient, path: str
) -> None:
    """docs/03-backend-architecture.md §10. The 429 carries Retry-After."""
    last = None
    for _ in range(21):
        last = await client.post(f"{V1}{path}", json={"email": _email(), "password": "x" * 12})
    assert last is not None
    assert last.status_code == 429
    assert last.json()["error"]["code"] == "RATE_LIMITED"
    assert int(last.headers["retry-after"]) > 0
