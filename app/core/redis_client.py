"""Redis client configuration and connection management."""

import logging

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

# Global Redis client instance
_redis_client: redis.Redis | None = None


async def get_redis_client() -> redis.Redis | None:
    """Get or create Redis client instance."""
    global _redis_client

    if not settings.REDIS_URL:
        return None

    if _redis_client is None:
        try:
            _redis_client = redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                max_connections=50,
                health_check_interval=30,
                socket_keepalive=True,
            )
            # Test connection
            await _redis_client.ping()
            logger.info("Redis client initialized successfully")
        except RedisError, OSError:
            # Handled: without Redis the rate limiter uses its in-memory window. The traceback is
            # logged so an unreachable Redis is visible, not just a one-line message.
            logger.exception("Failed to initialize Redis client")
            _redis_client = None

    return _redis_client


async def close_redis_client() -> None:
    """Close Redis client connection."""
    global _redis_client

    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis client closed")
