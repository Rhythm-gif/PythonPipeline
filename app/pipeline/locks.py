"""
PACR Pipeline — Distributed Locks
Implements MongoDB-backed distributed locking for cron jobs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Callable, Coroutine, Any

from pymongo.errors import DuplicateKeyError
import pymongo

from app.db.client import get_db
from app.common.logging import get_logger

logger = get_logger(__name__)


def locks_col():
    return get_db()["locks"]


async def init_locks_collection():
    """Ensure the lock collection has the necessary unique index."""
    await locks_col().create_index("name", unique=True)


async def with_lock(
    name: str,
    lock_at_most_for_ms: int,
    lock_at_least_for_ms: int,
    func: Callable[[], Coroutine[Any, Any, Any]]
) -> Any:
    """
    Executes a function if a distributed lock can be acquired.
    Matches the behavior of NestJS schedulerLockService.
    
    Args:
        name: Unique name for the lock.
        lock_at_most_for_ms: Maximum time the lock is held (failsafe if process dies).
        lock_at_least_for_ms: Minimum time before the lock can be acquired again (debounce).
        func: The async function to execute if the lock is acquired.
    """
    now = datetime.utcnow()
    
    # Try to acquire the lock. 
    # We can acquire if it doesn't exist OR if lock_until has expired.
    lock_doc = await locks_col().find_one_and_update(
        {
            "name": name,
            "$or": [
                {"lock_until": {"$lte": now}},
                {"lock_until": {"$exists": False}}
            ]
        },
        {
            "$set": {
                "name": name,
                "lock_until": now + timedelta(milliseconds=lock_at_most_for_ms),
                "locked_at": now,
            }
        },
        upsert=False, # We don't upsert here to avoid DuplicateKeyError if someone else has the lock
        return_document=pymongo.ReturnDocument.AFTER
    )

    if not lock_doc:
        # It might not exist at all yet. Let's try to insert it.
        try:
            await locks_col().insert_one({
                "name": name,
                "lock_until": now + timedelta(milliseconds=lock_at_most_for_ms),
                "locked_at": now,
            })
        except DuplicateKeyError:
            # Someone else just inserted it and acquired the lock.
            logger.info("Could not acquire lock (already locked)", lock_name=name)
            return None
    
    logger.info("Lock acquired", lock_name=name)
    
    try:
        # Execute the scheduled function
        return await func()
    finally:
        # Release the lock, but enforce lock_at_least_for_ms
        release_time = now + timedelta(milliseconds=lock_at_least_for_ms)
        await locks_col().update_one(
            {"name": name},
            {"$set": {"lock_until": release_time}}
        )
        logger.info("Lock released with debounce", lock_name=name, release_time=release_time)
