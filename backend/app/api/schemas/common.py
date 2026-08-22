"""Shared response conventions.

Contract §1: *"Field names are `camelCase`. The database is `snake_case`; the
API is not. Translation happens in the serialisation layer."* This module is
that layer — every schema in the package inherits from `ApiModel`, so nobody
has to remember an alias per field.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        # Serialise by alias by default so a route can `return Schema(...)`
        # without remembering `by_alias=True` and silently emitting snake_case.
        serialize_by_alias=True,
    )


class Page[ItemT](ApiModel):
    """Contract §1: cursor-based paging. `nextCursor` is null on the last page."""

    items: list[ItemT]
    next_cursor: str | None = None
