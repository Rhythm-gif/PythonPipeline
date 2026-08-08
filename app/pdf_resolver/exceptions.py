"""
PACR Pipeline — PDF Resolver Exception Hierarchy
"""
from __future__ import annotations


class PdfResolverError(Exception):
    """Base exception for all PDF resolver errors."""


class NoCandidatesError(PdfResolverError):
    """
    Raised when no Open Access signal is present for a paper.
    Resolution is skipped entirely — no network requests are made.
    """

