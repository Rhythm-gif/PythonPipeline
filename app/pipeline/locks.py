"""
PACR Pipeline — In-Memory Locks
Implements simple in-memory locking to prevent overlapping cron runs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Callable, Coroutine, Any

from app.common.logging import get_logger

logger = get_logger(__name__)

# In-memory lock store
_locks = {}
_lock_states = {}

async def init_locks_collection():
    """No-op for in-memory locks."""
    pass

async def with_lock(
    name: str,
    lock_at_most_for_ms: int,
    lock_at_least_for_ms: int,
    func: Callable[[], Coroutine[Any, Any, Any]]
) -> Any:
    """
    Executes a function if an in-memory lock can be acquired.
    """
    now = datetime.utcnow()
    
    if name not in _locks:
        _locks[name] = asyncio.Lock()
        
    lock = _locks[name]
    
    # Check if debounce time has passed
    if name in _lock_states:
        lock_until = _lock_states[name].get("lock_until")
        if lock_until and now < lock_until:
            logger.info("Could not acquire lock (debounce active)", lock_name=name)
            return None
            
    if lock.locked():
        logger.info("Could not acquire lock (already running)", lock_name=name)
        return None
        
    async with lock:
        logger.info("Lock acquired", lock_name=name)
        try:
            return await func()
        finally:
            release_time = datetime.utcnow() + timedelta(milliseconds=lock_at_least_for_ms)
            _lock_states[name] = {"lock_until": release_time}
            logger.info("Lock released with debounce", lock_name=name, release_time=release_time)
