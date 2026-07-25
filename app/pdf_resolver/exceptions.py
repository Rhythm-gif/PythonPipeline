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


class AllCandidatesFailedError(PdfResolverError):
    """
    Raised when every candidate URL has been tried and none
    yielded a valid PDF.
    """


class PdfValidationError(PdfResolverError):
    """
    Raised when content was received but the magic bytes do not
    match a valid PDF (%PDF header).
    """


class PdfDownloadError(PdfResolverError):
    """
    Raised on a network or HTTP-level failure during download.
    """
