import csv
import os
import re
from typing import Optional

from app.common.logging import get_logger

logger = get_logger(__name__)

# Cache for Scimago data: mapping normalized ISSN -> Q-value (e.g., "Q1")
_SCIMAGO_DATA: dict[str, str] = {}
_IS_LOADED = False

def _normalize_issn(issn: str) -> str:
    """Remove hyphens and uppercase to normalize ISSNs."""
    if not issn:
        return ""
    return re.sub(r'[^A-Z0-9]', '', str(issn).upper())

def load_scimago_data():
    """Load Scimago CSV into memory."""
    global _IS_LOADED, _SCIMAGO_DATA
    if _IS_LOADED:
        return

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_path = os.path.join(project_root, "data", "scimago.csv")

    if not os.path.exists(data_path):
        logger.warning(f"Scimago data file not found at {data_path}. Run scripts/download_scimago.py")
        _IS_LOADED = True
        return

    try:
        # Note: SJR sometimes uses semicolon delimiter depending on the download format, 
        # but often it is just comma separated. Let's try both or sniff.
        with open(data_path, "r", encoding="utf-8") as f:
            # Simple sniff
            first_line = f.readline()
            delimiter = ";" if ";" in first_line else ","
            f.seek(0)
            
            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                issn_field = row.get("Issn") or row.get("ISSN") or ""
                quartile = row.get("SJR Best Quartile") or row.get("Quartile") or row.get("Q") or ""
                
                # SJR Best Quartile can be empty or "-" for unranked
                if not quartile or quartile == "-":
                    continue
                
                # Issn field can contain multiple ISSNs comma-separated
                for issn in issn_field.split(","):
                    norm_issn = _normalize_issn(issn.strip())
                    if norm_issn:
                        _SCIMAGO_DATA[norm_issn] = quartile.upper()

        logger.info(f"Loaded {len(_SCIMAGO_DATA)} Scimago ISSN mappings.")
        _IS_LOADED = True
    except Exception as e:
        logger.error(f"Failed to load Scimago data: {e}")

def get_q_value(issn: Optional[str]) -> str:
    """
    Get the Q value (e.g. 'Q1') for a given ISSN.
    Returns 'Q4' (or 'Unranked') if not found, to default to rejection.
    """
    load_scimago_data()
    
    if not issn:
        return "Unranked"
        
    norm_issn = _normalize_issn(issn)
    return _SCIMAGO_DATA.get(norm_issn, "Unranked")
