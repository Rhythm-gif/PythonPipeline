"""
PACR Pipeline — HTML Landing Page Parser
Extracts PDF candidate URLs from publisher HTML landing pages.

Uses lxml (already a project dependency) — no additional packages required.
Searches for citation_pdf_url meta tags and PDF-signalling anchor links.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from lxml import etree

from app.common.logging import get_logger

logger = get_logger(__name__)

# Regex to detect PDF-related anchor text or href fragments
_PDF_LINK_RE = re.compile(
    r"(\.pdf$|[/_-]pdf[/_-]?|/download[/_]|full[._-]?text|full[._-]?article)",
    re.IGNORECASE,
)


def extract_pdf_urls_from_html(html: str, base_url: str = "") -> list[str]:
    """
    Parse an HTML landing page and return candidate PDF URLs.

    Search strategy (in order):
      1. ``<meta name="citation_pdf_url" content="...">``
      2. ``<meta property="citation_pdf_url" content="...">``
      3. ``<a href="...">`` whose href or link text suggests a PDF file

    Args:
        html:     Raw HTML string from the landing page response.
        base_url: The final URL of the page after redirects, used to
                  resolve relative hrefs to absolute URLs.

    Returns:
        Deduplicated list of candidate PDF URLs in discovery order.
        Empty list if nothing useful is found or parsing fails.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    try:
        parser = etree.HTMLParser(recover=True, encoding="utf-8")
        tree = etree.fromstring(html.encode("utf-8", errors="replace"), parser)
    except Exception as exc:
        logger.warning("HTML parse failed in PDF extractor", base_url=base_url, error=str(exc))
        return candidates

    def _add(raw_url: str) -> None:
        resolved = _resolve_url(raw_url, base_url)
        if resolved and resolved not in seen:
            seen.add(resolved)
            candidates.append(resolved)

    # ── 1 & 2. citation_pdf_url meta tags ────────────────────────────────────
    for meta in tree.findall(".//meta"):
        name = (meta.get("name") or meta.get("property") or "").strip().lower()
        content = (meta.get("content") or "").strip()
        if name == "citation_pdf_url" and content:
            _add(content)
            logger.debug("HTML parser: citation_pdf_url meta found", url=content, base=base_url)

    # ── 3. Anchor links with PDF signals ─────────────────────────────────────
    for anchor in tree.findall(".//a"):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        link_text = " ".join(anchor.itertext()).strip()
        combined = href + " " + link_text
        if _PDF_LINK_RE.search(combined):
            _add(href)
            logger.debug(
                "HTML parser: PDF anchor found",
                href=href,
                text=link_text[:60],
                base=base_url,
            )

    if candidates:
        logger.info(
            "HTML parser extracted PDF candidates",
            base_url=base_url,
            count=len(candidates),
            urls=candidates,
        )
    return candidates


def _resolve_url(url: str, base: str) -> str | None:
    """Resolve a potentially relative URL against *base*. Returns None if invalid."""
    if not url:
        return None
    try:
        resolved = urljoin(base, url) if base else url
        parsed = urlparse(resolved)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return resolved
    except Exception:
        pass
    return None
