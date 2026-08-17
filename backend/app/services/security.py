"""Passwords, access tokens and refresh tokens.

Three separate mechanisms, deliberately:

* **Passwords** are bcrypt at cost 12 (docs/03-backend-architecture.md §4.2).
* **Access tokens** are short-lived signed JWTs. Stateless, so no database read
  on every request; 15 minutes, so a leaked one stops working quickly.
* **Refresh tokens** are long-lived opaque strings, stored only as a SHA-256
  digest. Opaque rather than a JWT because they must be *revocable*, and a
  signed token that cannot be withdrawn is exactly what you do not want holding
  a 30-day session open.
"""

import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import bcrypt
import jwt

from app.api.errors import TokenExpiredError, TokenRevokedError
from app.config import settings

BCRYPT_ROUNDS: Final = 12
REFRESH_TOKEN_PREFIX: Final = "rt_"
ACCESS_TOKEN_TYPE: Final = "access"


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------


def _prehash(password: str) -> bytes:
    """SHA-256 the password before bcrypt sees it.

    bcrypt takes at most 72 bytes. Older bindings truncated silently — so
    "correct horse battery staple plus a long passphrase" and the same
    passphrase with a different ending were the *same password*. bcrypt 5
    raises instead, which turns that into a registration that fails for anyone
    with a long passphrase.

    Hashing first fixes both: every input becomes exactly 44 base64 bytes, no
    truncation, no error, and no ceiling on what a user may type. This is the
    same construction as passlib's `bcrypt_sha256`.

    base64 rather than raw digest bytes because a raw digest can contain a NUL,
    and bcrypt stops reading there.
    """
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), hashed.encode())
    except ValueError:
        # A malformed hash in the database must read as "wrong password", not
        # as a 500 that tells an attacker the row is special.
        return False


def dummy_verify() -> None:
    """Burn the same time as a real check, for an email that does not exist.

    Without this, "unknown email" answers measurably faster than "wrong
    password", and the login endpoint becomes a way to enumerate which
    addresses are registered — which is the thing the identical error message
    in contract §2 exists to prevent.
    """
    verify_password("password", _DUMMY_HASH)


_DUMMY_HASH: Final = hash_password("a-fixed-string-nobody-can-log-in-with")


# --------------------------------------------------------------------------
# Access tokens
# --------------------------------------------------------------------------


def _signing_key() -> str:
    if settings.jwt_algorithm == "RS256":
        with open(settings.jwt_private_key_path) as fh:
            return fh.read()
    return settings.jwt_secret_key


def _verifying_key() -> str:
    if settings.jwt_algorithm == "RS256":
        with open(settings.jwt_public_key_path) as fh:
            return fh.read()
    return settings.jwt_secret_key


def issue_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    """Return the token and its lifetime in seconds."""
    now = datetime.now(UTC)
    ttl = settings.access_token_ttl_seconds
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "typ": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, _signing_key(), algorithm=settings.jwt_algorithm)
    return token, ttl


def read_access_token(token: str) -> uuid.UUID:
    """Verify and return the subject, or raise the contract's 401."""
    try:
        payload = jwt.decode(token, _verifying_key(), algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        # A distinct code, because it is the only 401 the client should respond
        # to by refreshing rather than by signing the user out (contract §1).
        raise TokenExpiredError() from None
    except jwt.InvalidTokenError:
        raise TokenRevokedError() from None

    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        # A refresh token presented as a Bearer credential. Never valid: it is
        # opaque and unsigned, so this only fires on something forged.
        raise TokenRevokedError()
    try:
        return uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError):
        raise TokenRevokedError() from None


# --------------------------------------------------------------------------
# Refresh tokens
# --------------------------------------------------------------------------


def new_refresh_token() -> tuple[str, str]:
    """Return `(token, digest)`. Only the digest is ever stored."""
    token = REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    """SHA-256, not bcrypt.

    The token is 32 bytes of `secrets` output, so there is no dictionary to
    attack and no need for a slow hash — and a slow hash here would put a
    deliberate delay on every refresh.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)
