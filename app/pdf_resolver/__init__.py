"""
PACR Pipeline — PDF Resolver Package
Exports a process-wide singleton resolver instance.
"""
from app.pdf_resolver.resolver import PdfResolver, ResolverResult

# Singleton — shared across all pipeline runs within the same process.
# The internal InMemoryPdfCache persists DOI→result mappings between runs
# so the same DOI is never resolved twice in a single process lifetime.
pdf_resolver = PdfResolver()

__all__ = ["pdf_resolver", "PdfResolver", "ResolverResult"]
