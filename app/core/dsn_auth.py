import base64
import logging

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.services.app_service import AppService

logger = logging.getLogger(__name__)


async def extract_app_from_dsn(
    request: Request,
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> str:
    """
    Extract app_id from DSN-style authentication.

    Expected format:
    - Authorization: Basic <base64(public_id:)>

    Where public_id is the app's public identifier.
    """

    if not authorization:
        logger.warning("Missing Authorization header")
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    try:
        # Check if it's Basic auth
        if not authorization.startswith("Basic "):
            raise HTTPException(status_code=401, detail="Invalid authorization type")

        # Decode base64
        encoded = authorization.replace("Basic ", "")
        decoded = base64.b64decode(encoded).decode("utf-8")

        # Extract public_id (format is "public_id:" with empty password)
        if ":" in decoded:
            public_id = decoded.split(":")[0]
        else:
            public_id = decoded

        if not public_id:
            raise HTTPException(status_code=401, detail="Invalid authorization format")

        # Look up app by public_id
        app_service = AppService(db)
        app = await app_service.get_app_by_public_id(public_id)

        if not app:
            logger.warning("Invalid public_id", extra={"public_id": public_id})
            raise HTTPException(status_code=401, detail="Invalid app credentials")

        logger.info(
            "DSN authentication successful",
            extra={"app_id": app.app_id, "public_id": public_id},
        )
        return app.app_id

    except HTTPException:
        raise
    except Exception as e:
        logger.error("DSN authentication error", extra={"error": str(e)})
        raise HTTPException(status_code=401, detail="Invalid authorization")


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
