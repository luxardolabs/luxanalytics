"""The dashboard's request parameters, declared once: every route takes its time window and page
number through these types, so the bounds are checked where the request is parsed and the
services below never see a value no query can use."""

from typing import Annotated

from fastapi import Query

# Ten years of hours. The window is subtracted from "now"; far past this the date arithmetic
# underflows, and no stored event is that old.
MAX_HOURS = 24 * 365 * 10

# A page number far past any real list. `page` becomes a row offset, which the database takes only
# as a signed 64-bit integer.
MAX_PAGE = 1_000_000

# 0 is "all time".
Hours = Annotated[int, Query(ge=0, le=MAX_HOURS)]
Page = Annotated[int, Query(ge=1, le=MAX_PAGE)]
