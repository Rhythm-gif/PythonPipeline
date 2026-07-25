"""
PACR Pipeline — PDF Content Validators
Pure functions that inspect HTTP response headers and raw bytes.
Content-Type alone is not trusted — magic bytes are always checked.
"""
from __future__ import annotations

# %PDF magic bytes that begin every valid PDF file
_PDF_MAGIC = b"%PDF"

# MIME types that indicate a PDF body
_PDF_CONTENT_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "application/x-pdf",
    "application/acrobat",
    "application/vnd.pdf",
})

# MIME types that indicate an HTML landing page
_HTML_CONTENT_TYPES: frozenset[str] = frozenset({
    "text/html",
    "application/xhtml+xml",
})


def _base_content_type(content_type: str) -> str:
    """Strip parameters (e.g. charset) from a Content-Type header value."""
    return content_type.split(";")[0].strip().lower()


def is_pdf_content_type(content_type: str) -> bool:
    """Return True if the Content-Type header claims a PDF body."""
    return _base_content_type(content_type) in _PDF_CONTENT_TYPES


def is_html_content_type(content_type: str) -> bool:
    """Return True if the Content-Type header claims an HTML body."""
    return _base_content_type(content_type) in _HTML_CONTENT_TYPES


def has_pdf_magic_bytes(first_bytes: bytes) -> bool:
    """
    Return True if *first_bytes* begins with the ``%PDF`` magic sequence.

    This is the definitive check — Content-Type is advisory only.
    Always call this before committing to a full download.
    """
    return first_bytes[:4] == _PDF_MAGIC
