"""Query scoping.

PHASE1-TASKS.md, M2: *"Repository layer where every query filters on `user_id`
— a route that forgets is a data leak."*

The defence here is structural rather than a review habit. A `ScopedRepository`
cannot be built without a user, and every query it issues starts from
`_select()`, which has the filter already applied. There is no method that
returns an unfiltered statement, so forgetting the filter is not something a
caller can do by omission — it would take deliberately writing a new query
elsewhere.

`tests/test_repositories.py` proves the property from the outside: two users,
every method, nothing crosses.
"""

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.api.errors import APIError
from app.db import Base

#: Contract §1: cursor-based, never offsets — a list that changes while being
#: paged skips rows.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


class InvalidCursorError(APIError):
    status_code = 422
    code = "VALIDATION_ERROR"
    message = "That page cursor is not valid."


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    """Both halves of the sort key, so the page boundary is unambiguous.

    `created_at` alone is not enough: two rows written in the same millisecond
    would make the boundary ambiguous and one of them would be skipped or
    repeated.
    """
    raw = json.dumps({"t": created_at.isoformat(), "i": str(row_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        moment = datetime.fromisoformat(str(data["t"]))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment, uuid.UUID(str(data["i"]))
    except (KeyError, ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        raise InvalidCursorError() from None


class ScopedRepository[ModelT: Base]:
    """Reads and writes for one user's rows, and nobody else's."""

    model: ClassVar[Any]

    def __init__(self, session: AsyncSession, user_id: uuid.UUID) -> None:
        self._session = session
        self._user_id = user_id

    @property
    def user_id(self) -> uuid.UUID:
        return self._user_id

    def _select(self) -> Select[Any]:
        """The only entry point to a query. The filter is already on it."""
        return sa.select(self.model).where(self.model.user_id == self._user_id)

    async def get(self, row_id: uuid.UUID) -> ModelT | None:
        """Scoped by construction: another user's row reads as absent, which is
        what the caller should tell the client anyway."""
        result = await self._session.execute(self._select().where(self.model.id == row_id))
        found: ModelT | None = result.scalar_one_or_none()
        return found
