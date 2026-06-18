"""
PACR Pipeline — File Repository
Handles reading and writing the sync state to a local JSON file.
This completely replaces MongoDB for local state tracking.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

from app.common.logging import get_logger
from app.papers.models import PaperSource, SyncState

logger = get_logger(__name__)

# Save the state file in the root of the project
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "sync_time.json")

async def get_sync_state(source: PaperSource) -> Optional[SyncState]:
    """Retrieve the last sync state for a given source from the JSON file."""
    if not os.path.exists(STATE_FILE):
        return None
        
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        source_data = data.get(source.value)
        if source_data:
            return SyncState(
                source=source.value,
                last_sync=datetime.fromisoformat(source_data["last_sync"]),
                last_count=source_data.get("last_count", 0),
                last_error=source_data.get("last_error"),
                updated_at=datetime.fromisoformat(source_data["updated_at"]) if "updated_at" in source_data else datetime.utcnow()
            )
    except Exception as exc:
        logger.warning(f"Failed to read sync state file: {exc}")
        
    return None

async def update_sync_state(
    source: PaperSource,
    last_sync: datetime,
    count: int = 0,
    error: str | None = None,
) -> None:
    """
    Update the sync state for a given source in the JSON file.
    This overwrites the previous entry for this source, so the file never grows in size.
    """
    data = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass # If the file is corrupt, we'll just overwrite it
            
    # Overwrite the old date with the new date
    data[source.value] = {
        "source": source.value,
        "last_sync": last_sync.isoformat(),
        "last_count": count,
        "last_error": error,
        "updated_at": datetime.utcnow().isoformat()
    }
    
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as exc:
        logger.error(f"Failed to write sync state file: {exc}")
