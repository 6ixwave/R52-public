"""Offline regressions for response contracts, proxy feedback and search errors."""
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from r52.collectors import WBCollector, OzonCollector, AvitoCollector, FarpostCollector
from r52.collectors.aliexpress import AliExpressCollector
from r52.collectors.discovery import collect_links, SearchError
from r52.collectors.proxy_pool import ProxyPool


class CollectorContractsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="r52-contracts-")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        guard = patch.object(socket.socket, "connect", side_effect=AssertionError("NETWORK_FORBIDDEN"))
        guard.start()
        self.addCleanup(guard.stop)
        self.url = "https://www.aliexpress.com/wholesale?SearchText=offline"
        self.html = '<a href="/item/111.html">First</a><a href="/item/222.html">Second</a>'

    def collector(self, cls):
        c = cls(data_dir=self.home, use_proxies=False)
        c._throttle = MagicMock()
        self.addCleanup(c.session.close)
        return c

    def test_ali_jsonld_availability_is_an_exact_value(self):
        c = self.collector(AliExpressCollector)
        for value, expected in (("UNAVAILABLE", "OUT_OF_STOCK"),
                ("https://schema.org/OutOfStock", "OUT_OF_STOCK"),
                ("http://schema.org/InStock", "IN_STOCK"),
                ("https://schema.org/PreOrder", "PRE_ORDER"),
                ("IN_STOCK", "IN_STOCK"), ("SOLD_OUT", "OUT_OF_STOCK"),
                ("AVAILABLE", "IN_STOCK"), ("not available", "UNKNOWN"),
                ("https://foreign.invalid/InStock", "UNKNOWN")):
            with self.subTest(value=value):
                obs = {"fields": {}, "price_candidates": [], "media": {"items": []},
                       "availability": "UNKNOWN"}
                html = '<script type="application/ld+json">' + json.dumps({
                    "@type": "Product", "name": "Offline GPU", "offers": {"availability": value}
                }) + '</script>'
                c._parse_jsonld(html, obs)
                self.assertEqual(obs["availability"], expected)

    def test_ali_html_does_not_use_script_or_attribute_stock_labels(self):
        c = self.collector(AliExpressCollector)
        for html, expected in ((
                '<h1>Offline GPU</h1><script>translations={sold:"Out of stock"}</script>', "UNKNOWN"),
                ('<h1>Offline GPU</h1><div data-label="available"></div>', "UNKNOWN"),
                ('<h1>Offline GPU</h1><!-- sold out --><style>.available{color:red}</style>', "UNKNOWN"),
                ('<h1>Offline GPU</h1><script>"sold out"</script><div>In stock</div>', "IN_STOCK"),
                ('<h1>Offline GPU</h1><div>Out of stock</div>', "OUT_OF_STOCK"),
                ('<h1>Offline GPU</h1><div>Pre-order</div>', "PRE_ORDER")):
            with self.subTest(html=html):
                obs = {"fields": {}, "price_candidates": [], "media": {"items": []},
                       "availability": "UNKNOWN"}
                c._parse_html(html, obs)
                self.assertEqual(obs["availability"], expected)

    def test_listing_response_and_browser_dict(self):
        for response in (SimpleNamespace(status_code=200, url=self.url, text=self.html),
                         {"status": 200, "final_url": self.url, "html": self.html}):
            with self.subTest(kind=type(response).__name__):
                c = SimpleNamespace(fetch=lambda u: response,
                                    collect_product=lambda id, url: {"observation": {"id": id}})
                self.assertEqual(collect_links(c, "ALIEXPRESS", self.url, 1, page=2)[0]["observation"]["id"], "222")

    def test_bad_listing_contracts_are_explicit(self):
        for response, code in ((None, "LISTING_HTTP_UNAVAILABLE"),
                ({"status": 403}, "LISTING_HTTP_403"),
                ({"status": 200, "html": self.html}, "UNEXPECTED_REDIRECT"),
                ({"status": 200, "html": self.html, "final_url": "https://foreign.invalid/"}, "UNEXPECTED_REDIRECT"),
                ({"status": 200, "final_url": self.url}, "LISTING_HTML_MISSING"),
                ({"status": 200, "final_url": self.url, "html": "<title>captcha</title>"}, "ACCESS_CHALLENGE")):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "^" + code + "$"):
                collect_links(SimpleNamespace(fetch=lambda u: response), "ALIEXPRESS", self.url, 1)

    def test_browser_producer_feeds_listing_consumer(self):
        c = self.collector(AliExpressCollector)
        page = MagicMock()
        page.goto.return_value = SimpleNamespace(status=200)
        page.evaluate.return_value = {"h1": "Offline product title with enough text"}
        page.content.return_value = self.html
        page.url = self.url
        c._page = page
        c.fetch = c._fetch_pw
        c.collect_product = lambda id, url: {"observation": {"id": id}}
        rows = collect_links(c, "ALIEXPRESS", self.url, 2)
        self.assertEqual([r["observation"]["id"] for r in rows], ["111", "222"])

    def test_http_errors_not_empty_success(self):
        for cls in (WBCollector, OzonCollector, AvitoCollector, FarpostCollector):
            for code in (403, 404, 429, 500):
                with self.subTest(source=cls.source, code=code):
                    c = self.collector(cls)
                    response = SimpleNamespace(status_code=code)
                    c._http_get = MagicMock(return_value=response)
                    c._cffi = None
                    c._warmup = MagicMock()
                    c.session.get = MagicMock(return_value=response)
                    with self.assertRaisesRegex(SearchError, f"^SEARCH_HTTP_{code}$"):
                        c.collect_by_search("offline", page_size=1)

    def test_request_errors_propagate(self):
        for cls in (OzonCollector, AvitoCollector, FarpostCollector):
            with self.subTest(source=cls.source):
                c = self.collector(cls)
                c._cffi = None
                c._warmup = MagicMock()
                c.session.get = MagicMock(side_effect=TimeoutError("offline timeout"))
                with self.assertRaisesRegex(SearchError, "^SEARCH_REQUEST_FAILED$"):
                    c.collect_by_search("offline")

    def test_short_response_not_empty_success(self):
        for cls in (OzonCollector, AvitoCollector, FarpostCollector):
            with self.subTest(source=cls.source):
                c = self.collector(cls)
                c._cffi = None
                c._warmup = MagicMock()
                c.session.get = MagicMock(return_value=SimpleNamespace(status_code=200, text="oops", headers={}, url="https://www.farpost.ru/dir"))
                with self.assertRaisesRegex(SearchError, "^SEARCH_RESPONSE_TOO_SHORT$"):
                    c.collect_by_search("offline")

    def test_wb_nested_prices_preserve_variants(self):
        c = self.collector(WBCollector)
        product = {"id": 12345, "name": "Offline GPU", "sizes": [
            {"chrtId": 101, "price": {"product": 1234500, "basic": 1500000}},
            {"chrtId": 102, "price": {"product": 2345600}}]}
        c._http_get = MagicMock(return_value=SimpleNamespace(status_code=200, json=lambda: {"products": [product]}))
        obs = c.collect_by_search("offline")[0]["observation"]
        self.assertEqual([p["amount_minor"] for p in obs["price_candidates"]], [1234500, 1500000, 2345600])
        self.assertEqual([p["variant_id"] for p in obs["price_candidates"]], ["101", "101", "102"])
        self.assertTrue(all(not p["usable_purchase_price"] for p in obs["price_candidates"]))
        self.assertEqual(obs["availability"], "UNKNOWN")

    def test_farpost_route_pagination_and_challenge(self):
        c = self.collector(FarpostCollector)
        c._warmup = MagicMock()
        c._cffi = None
        c.session.get = MagicMock(return_value=SimpleNamespace(
            status_code=200, text="verify", headers={}, url="https://www.farpost.ru/verify?u=dir"))
        for page in (1, 2):
            with self.assertRaisesRegex(SearchError, "^SEARCH_ACCESS_CHALLENGE$"):
                c.collect_by_search("laptop", page=page)
        self.assertEqual([call.args[0] for call in c.session.get.call_args_list],
                         ["https://www.farpost.ru/dir?query=laptop&page=1", "https://www.farpost.ru/dir?query=laptop&page=2"])

    def test_wb_malformed_price_does_not_drop_other_product(self):
        c = self.collector(WBCollector)
        products = [{"id": 1, "name": "Bad", "price": {"product": "garbage", "basic": "NaN", "sale": "1e999999"}},
                    {"id": 2, "name": "Valid", "price": 32100}]
        c._http_get = MagicMock(return_value=SimpleNamespace(status_code=200, json=lambda: {"products": products}))
        rows = c.collect_by_search("offline")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["observation"]["price_candidates"], [])
        self.assertEqual(rows[1]["observation"]["price_candidates"][0]["amount_minor"], 32100)

    def test_wb_invalid_json_is_explicit(self):
        c = self.collector(WBCollector)
        c._http_get = MagicMock(return_value=SimpleNamespace(status_code=200, json=MagicMock(side_effect=ValueError())))
        with self.assertRaisesRegex(SearchError, "^SEARCH_INVALID_JSON$"):
            c.collect_by_search("offline")


class ProxyFeedbackTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="r52-proxy-feedback-")
        self.addCleanup(temp.cleanup)
        self.pool = ProxyPool(data_dir=Path(temp.name))
        self.pool.add_proxies(["http://user:private@127.0.0.1:19001", "http://127.0.0.1:19002"])

    def test_feedback_targets_selected_proxy(self):
        selected = self.pool.get_proxy()
        self.pool.mark_failure()
        self.pool.mark_success(0.25)
        for entry in self.pool._entries:
            self.assertEqual(entry.failures, 1 if entry.url == selected["https"] else 0)
            self.assertEqual(entry.latency_ms, 250 if entry.url == selected["https"] else 0)

    def test_browser_can_report_original_proxy(self):
        original = self.pool.get_proxy()
        self.pool.get_proxy()
        self.pool.mark_failure(proxies=original)
        self.pool.mark_success(0.5, proxies=original)
        self.assertEqual(self.pool._entries[0].failures, 1)
        self.assertEqual(self.pool._entries[0].latency_ms, 500)

    def test_explicit_direct_does_not_penalize_proxy(self):
        self.pool.get_proxy()
        self.pool.mark_failure(proxies=None)
        self.assertEqual(sum(e.failures for e in self.pool._entries), 0)

    def test_warning_does_not_contain_credentials(self):
        selected = self.pool.get_proxy()
        with self.assertLogs("r52.collectors.proxy", level="WARNING") as captured:
            for _ in range(5):
                self.pool.mark_failure(proxies=selected)
        self.assertNotIn("private", str(captured.output))
        self.assertNotIn("user", str(captured.output))

    def test_feedback_is_thread_local(self):
        selected = self.pool.get_proxy()
        thread = threading.Thread(target=self.pool.mark_failure)
        thread.start()
        thread.join(timeout=3)
        self.assertEqual(sum(e.failures for e in self.pool._entries), 0)
        self.pool.mark_failure()
        self.assertEqual(next(e.failures for e in self.pool._entries if e.url == selected['https']), 1)
