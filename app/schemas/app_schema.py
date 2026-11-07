from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


def _no_nul(value: str | None) -> str | None:
    """Text the database stores cannot hold a NUL byte."""
    if value is not None and "\x00" in value:
        raise ValueError("must not contain a NUL byte")
    return value


# A name written by a form: surrounding whitespace is dropped and something visible must remain.
# Only the write models use it; AppRow reads a stored name as it is, so an app stored with a blank
# name still lists and opens.
AppName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class AppBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    organization: str | None = Field(None, max_length=255)
    description: str | None = None
    app_metadata: dict[str, Any] = Field(default_factory=dict)

    _text_has_no_nul = field_validator("name", "organization", "description")(_no_nul)


class AppCreate(AppBase):
    name: AppName
    # Lowercase letters, digits, underscores and hyphens, starting with a letter or digit: an
    # app_id is a URL path segment, a cookie value and a query value, so it carries none of the
    # characters those need escaped. Checked at creation only; stored app_ids are read as they are.
    app_id: str = Field(
        ..., min_length=1, max_length=255, pattern=r"^[a-z0-9][a-z0-9_-]*$"
    )


class AppUpdate(BaseModel):
    name: AppName | None = None
    organization: str | None = Field(None, max_length=255)
    description: str | None = None
    is_active: bool | None = None
    app_metadata: dict[str, Any] | None = None

    _text_has_no_nul = field_validator("name", "organization", "description")(_no_nul)


class AppEditForm(BaseModel):
    """The edit form as the browser posts it: every field optional and unconstrained, because the
    limits live on AppUpdate, which answers a refused field in the form's own error box. A text
    field the browser sent empty is "" (the person cleared it); one it did not send is None
    (leave it). The Active checkbox is omitted when unchecked, so absent means False."""

    name: str | None = None
    organization: str | None = None
    description: str | None = None
    is_active: bool = False


class AppRow(AppBase):
    """An app's stored columns, read from the ORM row (no derived fields). A stored row is always
    readable: the name carries no length rule here (the write models hold them), so one row with
    an empty name cannot fail the list, the export or a panel."""

    name: str
    id: UUID
    public_id: str
    project_id: str
    app_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class AppUrls(BaseModel):
    """Finished hrefs for one app's actions, resolved by route name in the view."""

    detail: str
    edit: str
    update: str
    dashboard: str


class AppResponse(AppRow):
    """An app as the dashboard shows it: its columns plus the DSN the SDK is configured with."""

    dsn: str
    urls: AppUrls


class AppListItem(BaseModel):
    """One row of the apps list."""

    app_id: str
    name: str
    organization: str | None
    is_active: bool
    public_id: str
    dsn: str
    event_count: int
    urls: AppUrls


class AppStats(BaseModel):
    total_events: int
    unique_users: int
    unique_sessions: int
