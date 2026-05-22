"""MongoDB connection and collection access (Motor async driver)."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorCollection

logger = logging.getLogger(__name__)

_client = None
_db = None
_mongo_available = True


def _get_mongo_uri() -> str:
    return os.getenv("MONGODB_URI", "mongodb://localhost:27017")


def _get_db_name() -> str:
    return os.getenv("MONGODB_DB", "gemini_pixel_offer_claim_bot")


def get_client():
    """Return (or create) the shared Motor client."""
    global _client
    if _client is None:
        from motor.motor_asyncio import AsyncIOMotorClient

        uri = _get_mongo_uri()
        _client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        logger.info("MongoDB client created → %s / %s", uri.split("@")[-1], _get_db_name())
    return _client


def get_db():
    """Return the bot's Motor database handle."""
    global _db
    if _db is None:
        _db = get_client()[_get_db_name()]
    return _db


def users_col() -> "AsyncIOMotorCollection":
    """The `users` collection — one document per Telegram user."""
    return get_db()["users"]


def set_mongo_available(value: bool) -> None:
    global _mongo_available
    _mongo_available = value


def mongo_available() -> bool:
    return _mongo_available


async def ensure_indexes() -> None:
    """Create indexes once at startup."""
    col = users_col()
    # _id is already indexed by MongoDB (we use telegram_id as _id).
    # Index on jobs.id for fast per-job lookups.
    await col.create_index("jobs.id", background=True, sparse=True)
    logger.info("MongoDB indexes ensured.")


async def ping() -> bool:
    """Return True if MongoDB is reachable."""
    try:
        await get_client().admin.command("ping")
        set_mongo_available(True)
        return True
    except Exception as exc:
        set_mongo_available(False)
        logger.error("MongoDB ping failed: %s", exc)
        return False
