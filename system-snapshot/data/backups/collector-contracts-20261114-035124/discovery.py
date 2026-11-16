"""Discover listing IDs from public category pages using the source's own card reader."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from ..domain.normalize import HOSTS, ID_PATTERNS


def source_url(source, url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in HOSTS[source]
                and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except ValueError:
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
    if not source_url(source, url):
        raise ValueError("URL_SOURCE_MISMATCH")
    response = collector.fetch(url)
    if response is None or response.status_code != 200:
        raise ValueError("LISTING_HTTP_" + str(getattr(response, "status_code", "UNAVAILABLE")))
    if not source_url(source, response.url):
        raise ValueError("UNEXPECTED_REDIRECT")
    html = response.text
    if "captcha" in html[:6000].lower() and "<title" in html[:6000].lower():
        raise ValueError("ACCESS_CHALLENGE")
    parser = ListingLinks(source, url, page * limit)
    parser.feed(html)
    results = []
    for listing_id, link in list(parser.links.items())[(page-1)*limit:page*limit]:
        item = collector.collect_product(listing_id, link)
        if not item or item.get("observation", {}).get("status") in ("FAILED", "BLOCKED"):
            break
        results.append(item)
    return results

