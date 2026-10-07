import base64
import logging
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.services.core.app_core_service import AppCoreService

logger = logging.getLogger(__name__)


async def extract_app_from_dsn(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """
    Extract app_id from DSN-style authentication.

    Expected format:
    - Authorization: Basic <base64(public_id:)>

    Where public_id is the app's public identifier. A malformed header is the caller's error (401);
    anything else (the app lookup failing, say) is a server error and propagates as one.
    """
    if not authorization:
        logger.warning("Missing Authorization header")
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Invalid authorization type")

    try:
        decoded = base64.b64decode(authorization.removeprefix("Basic ")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as e:
        # b64decode raises binascii.Error (a ValueError); .decode raises UnicodeDecodeError.
        logger.warning("Malformed authorization header", extra={"error": str(e)})
        raise HTTPException(status_code=401, detail="Invalid authorization") from e

    # The format is "public_id:" with an empty password.
    public_id = decoded.split(":")[0]
    if not public_id:
        raise HTTPException(status_code=401, detail="Invalid authorization format")

    app = await AppCoreService(db).get_app_by_public_id(public_id)
    if not app:
        logger.warning("Invalid public_id", extra={"public_id": public_id})
        raise HTTPException(status_code=401, detail="Invalid app credentials")

    logger.info(
        "DSN authentication successful",
        extra={"app_id": app.app_id, "public_id": public_id},
    )
    return app.app_id


def extract_app_from_path(path: str) -> str | None:
    """
    Alternative: Extract public_id from URL path.

    For URLs like: POST /api/v1/events/public_id
    """
    parts = path.strip("/").split("/")
    if (
        len(parts) >= 4
        and parts[0] == "api"
        and parts[1] == "v1"
        and parts[2] == "events"
    ):
        return parts[3] if len(parts) > 3 else None
    return None


async def verify_project_dsn(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """The app_id for POST /api/v1/events/{project_id}: the project names the app, and the
    Basic-auth username must be that app's public_id (Sentry's DSN pattern).

    DSN format: https://PUBLIC_ID@analytics.domain.com/api/v1/events/PROJECT_ID
    """
    app = await AppCoreService(db).get_app_by_project_id(project_id)
    if not app:
        logger.warning("Invalid project_id in URL", extra={"project_id": project_id})
        raise HTTPException(status_code=404, detail="Invalid project")

    if not authorization or not authorization.startswith("Basic "):
        logger.warning("Missing authorization header", extra={"project_id": project_id})
        raise HTTPException(status_code=401, detail="Authorization required")

    try:
        decoded = base64.b64decode(authorization.removeprefix("Basic ")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as e:
        # b64decode raises binascii.Error (a ValueError); .decode raises UnicodeDecodeError.
        logger.warning("Malformed authorization header", extra={"error": str(e)})
        raise HTTPException(status_code=401, detail="Invalid authorization") from e

    auth_public_id = decoded.split(":")[0]
    if not secrets.compare_digest(auth_public_id.encode(), app.public_id.encode()):
        logger.warning(
            "Invalid public_id for project",
            extra={
                "project_id": project_id,
                "expected_public_id": app.public_id[:8] + "...",
                "provided_public_id": auth_public_id[:8] + "...",
            },
        )
        raise HTTPException(status_code=401, detail="Invalid DSN key")

    logger.info(
        "Processing events via project endpoint",
        extra={"app_id": app.app_id, "project_id": project_id},
    )
    return app.app_id
