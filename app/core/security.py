import hashlib
import hmac
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.hmac_replay import mark_signature, signature_is_marked
from app.core.rate_limiter import enforce_app_rate_limit
from app.db.database import get_db
from app.services.core.app_core_service import AppCoreService

logger = logging.getLogger(__name__)


def verify_hmac_signature(
    request: Request,
    x_signature: str = Header(..., alias="X-HMAC-Signature"),
    x_key_id: str = Header(..., alias="X-Key-ID"),
    x_timestamp: str = Header(..., alias="X-Timestamp"),
) -> str:
    """Verify HMAC signature and return app_id."""

    # Check timestamp (prevent replay attacks)
    try:
        timestamp = int(x_timestamp)
        current_time = int(time.time())
        if abs(current_time - timestamp) > settings.EVENT_TIMESTAMP_FUTURE_TOLERANCE:
            raise HTTPException(status_code=401, detail="Request timestamp too old")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp") from None

    # Get HMAC secret for this key_id
    hmac_keys = settings.hmac_keys_dict
    secret = hmac_keys.get(x_key_id)
    if not secret:
        logger.warning("Invalid key ID", extra={"key_id": x_key_id})
        raise HTTPException(status_code=401, detail="Invalid key ID")

    # Get raw body for signature verification
    body = getattr(request.state, "raw_body", b"")

    # Create signature: HMAC-SHA256(body + timestamp, secret)
    message = body + x_timestamp.encode()
    computed_signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

    # Verify signature
    if not hmac.compare_digest(computed_signature.encode(), x_signature.encode()):
        logger.warning(
            "HMAC signature verification failed",
            extra={
                "key_id": x_key_id,
                "computed": computed_signature[:8] + "...",
                "provided": x_signature[:8] + "...",
            },
        )
        raise HTTPException(status_code=403, detail="Invalid signature")

    logger.info("HMAC signature verified", extra={"app_id": x_key_id})
    return x_key_id


async def _refuse_deleted(app_id: str, db: AsyncSession) -> None:
    app = await AppCoreService(db).get_app_by_app_id_including_deleted(app_id)
    if app is not None and app.deleted_at is not None:
        logger.warning(
            "HMAC request for a deleted app refused", extra={"app_id": app_id}
        )
        raise HTTPException(status_code=403, detail="App is deleted")


async def verify_hmac_app(
    app_id: Annotated[str, Depends(verify_hmac_signature)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> str:
    """A verified HMAC key of an app that is not deleted, counted against its app's rate limit (the
    stats read)."""
    await _refuse_deleted(app_id, db)
    await enforce_app_rate_limit(app_id)
    return app_id


async def verify_hmac_ingest(
    app_id: Annotated[str, Depends(verify_hmac_signature)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> str:
    """A verified HMAC key may ingest unless its app is marked Inactive or has been deleted: both
    stop HMAC ingest as they stop DSN ingest. A key with no app row is still accepted: HMAC_KEYS is
    the authority for HMAC-only clients."""
    app = await AppCoreService(db).get_app_by_app_id_including_deleted(app_id)
    if app is not None and (not app.is_active or app.deleted_at is not None):
        logger.warning(
            "HMAC ingest for an inactive or deleted app refused",
            extra={"app_id": app_id},
        )
        raise HTTPException(status_code=403, detail="App is inactive")
    return app_id


async def hmac_replay_mark(
    x_key_id: Annotated[str | None, Header(alias="X-Key-ID")] = None,
    x_signature: Annotated[str | None, Header(alias="X-HMAC-Signature")] = None,
) -> AsyncIterator[bool]:
    """Yields whether this signed request was already stored inside its window (a replay). Marks
    the signature once the request has completed, which is after get_db committed: this dependency
    must be resolved before get_db so that it exits after it. A request that fails, is cancelled or
    dies before the commit leaves no mark, so its retry is stored."""
    if x_key_id is None or x_signature is None:
        yield False
        return
    replayed = await signature_is_marked(x_key_id, x_signature)
    yield replayed
    if not replayed:
        await mark_signature(x_key_id, x_signature)
