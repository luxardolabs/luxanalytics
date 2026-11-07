"""The web routes' request parameters, declared once: every dashboard and apps route takes its
time window, page number, text filters and path values through these types, so the bounds are
checked where the request is parsed and the services below never see a value no query can use."""

from typing import Annotated
from urllib.parse import quote

from fastapi import Form, Path, Query
from pydantic import AfterValidator, BeforeValidator, StringConstraints

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

# The nav selector keeps the chosen app_id, percent-encoded, in a cookie, and a browser keeps a
# cookie of about 4 KB at most (name, value and attributes together). The encoded id may use this
# much of it. An app_id longer than 255 characters can exist (events carry any id), so the limit
# is the cookie's, not the apps column's.
MAX_ENCODED_APP_ID = 3500


def _fits_a_cookie(value: str) -> str:
    if len(quote(value, safe="")) > MAX_ENCODED_APP_ID:
        raise ValueError("too long to keep in a cookie")
    return value


AppIdForm = Annotated[str, Form(pattern=_NO_NUL), AfterValidator(_fits_a_cookie)]
