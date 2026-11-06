"""The dashboard's request parameters, declared once: every route takes its time window, page
number and text filters through these types, so the bounds are checked where the request is parsed
and the services below never see a value no query can use."""

from typing import Annotated

from fastapi import Form, Path, Query
from pydantic import BeforeValidator, StringConstraints

# Ten years of hours. The window is subtracted from "now"; far past this the date arithmetic
# underflows, and no stored event is that old.
MAX_HOURS = 24 * 365 * 10

# A page number far past any real list. `page` becomes a row offset, which the database takes only
# as a signed 64-bit integer.
MAX_PAGE = 1_000_000

# 0 is "all time".
Hours = Annotated[int, Query(ge=0, le=MAX_HOURS)]
Page = Annotated[int, Query(ge=1, le=MAX_PAGE)]

# Postgres text cannot hold a NUL byte: one in a filter or a path value is a driver error at the
# query. It is refused at parse time (422), for the query string and for the path alike.
_NO_NUL = r"^[^\x00]*$"


def empty_to_none(value: object) -> object:
    """A filter form's 'All' option submits an empty value: that is no filter."""
    return None if value == "" else value


TextFilter = Annotated[
    Annotated[str, StringConstraints(pattern=_NO_NUL)] | None,
    BeforeValidator(empty_to_none),
    Query(),
]
PathText = Annotated[str, Path(pattern=_NO_NUL)]
# An app_id as a form field: no longer than the column that stores it.
AppIdForm = Annotated[str, Form(pattern=_NO_NUL, max_length=255)]
