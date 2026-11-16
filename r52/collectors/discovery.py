"""Discover listing IDs from public category pages using the source's own card reader."""
import logging
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from ..domain.normalize import HOSTS, ID_PATTERNS

log = logging.getLogger("r52.collectors.discovery")


class SearchError(ValueError):
    """Stable worker error code, distinct from a genuine empty search result."""


def source_url(source, url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in HOSTS.get(source, ())
                and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except (ValueError, TypeError, AttributeError):
        return False


class ListingLinks(HTMLParser):
    def __init__(self, source, base, limit):
        super().__init__()
        self.source, self.base, self.limit = source, base, limit
        self.links = {}

    def handle_starttag(self, tag, attrs):
        if tag != "a" or len(self.links) >= self.limit:
            return
        url = urljoin(self.base, dict(attrs).get("href") or "")
        if source_url(self.source, url):
            match = ID_PATTERNS[self.source].search(urlsplit(url).path)
            if match:
                listing_id = next(g for g in match.groups() if g)
                self.links.setdefault(listing_id, url.split("#")[0])


def collect_links(collector, source, url, limit, page=1):
    if not isinstance(limit, int) or not isinstance(page, int) or limit < 1 or page < 1:
        raise ValueError("INVALID_LISTING_RANGE")
    if not source_url(source, url):
        raise ValueError("URL_SOURCE_MISMATCH")
    response = collector.fetch(url)
    if isinstance(response, dict):
        status = response.get("status")
        final_url = response.get("final_url")
        html = response.get("html")
    else:
        status = getattr(response, "status_code", None)
        final_url = getattr(response, "url", None)
        html = getattr(response, "text", None)
    if status != 200:
        raise ValueError("LISTING_HTTP_" + str(status if isinstance(status, int) else "UNAVAILABLE"))
    if not source_url(source, final_url):
        raise ValueError("UNEXPECTED_REDIRECT")
    if not isinstance(html, str) or not html.strip():
        raise ValueError("LISTING_HTML_MISSING")
    if "captcha" in html[:6000].lower() and "<title" in html[:6000].lower():
        raise ValueError("ACCESS_CHALLENGE")
    parser = ListingLinks(source, final_url, page * limit)
    parser.feed(html)
    results = []
    for listing_id, link in list(parser.links.items())[(page-1)*limit:page*limit]:
        item = collector.collect_product(listing_id, link)
        if not item or item.get("observation", {}).get("status") in ("FAILED", "BLOCKED"):
            log.info("discovery %s: skip %s (status=%s)", source, listing_id,
                     item.get("observation", {}).get("status") if item else "NO_RESULT")
            continue
        results.append(item)
    return results

