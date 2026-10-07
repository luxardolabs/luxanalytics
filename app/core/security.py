import hashlib
import hmac
import time

import structlog
from fastapi import Header, HTTPException, Request

from app.core.config import settings

logger = structlog.get_logger()


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
        raise HTTPException(status_code=401, detail="Invalid timestamp")

    # Get HMAC secret for this key_id
    hmac_keys = settings.hmac_keys_dict
    secret = hmac_keys.get(x_key_id)
    if not secret:
        logger.warning("Invalid key ID", key_id=x_key_id)
        raise HTTPException(status_code=401, detail="Invalid key ID")

    # Get raw body for signature verification
    body = getattr(request.state, "raw_body", b"")

    # Create signature: HMAC-SHA256(body + timestamp, secret)
    message = body + x_timestamp.encode()
    computed_signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

    # Verify signature
    if not hmac.compare_digest(computed_signature, x_signature):
        logger.warning(
            "HMAC signature verification failed",
            key_id=x_key_id,
            computed=computed_signature[:8] + "...",
            provided=x_signature[:8] + "...",
        )
        raise HTTPException(status_code=403, detail="Invalid signature")

    logger.info("HMAC signature verified", app_id=x_key_id)
    return x_key_id


def get_app_id_from_headers(headers: dict) -> str:
    """Extract app_id from request headers without full verification."""
    return headers.get("x-key-id", "")
