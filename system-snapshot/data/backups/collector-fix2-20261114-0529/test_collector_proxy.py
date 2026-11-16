"""Proxy forwarding contracts. No marketplace traffic or owner profiles."""
import json
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from r52.collectors import WBCollector, OzonCollector, AvitoCollector, FarpostCollector
from r52.collectors.aliexpress import AliExpressCollector, AliExpressRUCollector
from r52.collectors.pw_fetch import PwFetcher, playwright_proxy


PROXIES = {"http": "http://REDACTED@127.0.0.1:18080",
           "https": "http://REDACTED@127.0.0.1:18080"}
BROWSER_PROXY = {"server": "http://127.0.0.1:18080", "username": "demo", "password": "[REDACTED_AUTH_SECRET]"}


class ProxyForwardingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="r52-proxy-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        # Every transport is mocked in this class; unexpected sockets fail immediately.
        guard = patch.object(socket.socket, "connect", side_effect=AssertionError("NETWORK_FORBIDDEN"))
        guard.start()
        self.addCleanup(guard.stop)
        self.response = SimpleNamespace(status_code=200, text=" " * 110001,
                                        headers={}, json=lambda: {}, url="https://example.invalid/")

    def collector(self, cls):
        collector = cls(data_dir=self.home, use_proxies=True)
        collector.pool.get_proxy = MagicMock(return_value=PROXIES)
        collector._throttle = MagicMock()
        self.addCleanup(collector.session.close)
        return collector

    def test_wb_both_transports(self):
        for cffi in (True, False):
            with self.subTest(cffi=cffi):
                collector = self.collector(WBCollector)
                transport = MagicMock()
                transport.get.return_value = self.response
                collector._cffi = transport if cffi else None
                if not cffi:
                    collector.session.get = transport.get
                collector._http_get("https://example.invalid/product")
                self.assertEqual(transport.get.call_args.kwargs["proxies"], PROXIES)

    def test_ozon_search_both_transports(self):
        for cffi in (True, False):
            with self.subTest(cffi=cffi):
                collector = self.collector(OzonCollector)
                transport = MagicMock()
                transport.get.return_value = self.response
                collector._cffi = transport if cffi else None
                if not cffi:
                    collector.session.get = transport.get
                collector.collect_by_search("offline", page_size=1)
                self.assertEqual(transport.get.call_args.kwargs["proxies"], PROXIES)

    def test_ozon_composer_all_requests(self):
        collector = self.collector(OzonCollector)
        collector._cffi = MagicMock()
        collector._cffi.get.return_value = self.response
        collector._try_composer_api("123", "https://www.ozon.ru/product/123/", "")
        self.assertTrue(collector._cffi.get.called)
        for call in collector._cffi.get.call_args_list:
            self.assertEqual(call.kwargs["proxies"], PROXIES)

    def test_ozon_card_http_fallback(self):
        collector = self.collector(OzonCollector)
        collector._get_pw = MagicMock(side_effect=RuntimeError("OFFLINE_BROWSER"))
        collector._cffi = MagicMock()
        collector._cffi.get.return_value = self.response
        collector.parse_page = MagicMock(return_value={"fields": {"title": {"value": "Test GPU"}}, "price_candidates": []})
        self.assertIsNotNone(collector.collect_product("123", "https://www.ozon.ru/product/123/"))
        self.assertEqual(collector._cffi.get.call_args.kwargs["proxies"], PROXIES)

    def test_ozon_browser_receives_proxy(self):
        collector = self.collector(OzonCollector)
        with patch("r52.collectors.ozon.PwFetcher") as fetcher:
            collector._get_pw()
            collector._get_pw()
            fetcher.assert_called_once_with(proxies=PROXIES)

    def test_farpost_warmup(self):
        collector = self.collector(FarpostCollector)
        collector.session.get = MagicMock(return_value=self.response)
        with patch("r52.collectors.farpost.time.sleep"):
            collector._warmup()
        self.assertEqual(collector.session.get.call_args.kwargs["proxies"], PROXIES)

    def test_avito_farpost_search_existing_forwarding(self):
        for cls in (AvitoCollector, FarpostCollector):
            with self.subTest(source=cls.source):
                collector = self.collector(cls)
                collector.session.get = MagicMock(return_value=self.response)
                collector._cffi = None
                collector._warmup = MagicMock()
                collector.collect_by_search("offline", page_size=1)
                self.assertEqual(collector.session.get.call_args.kwargs["proxies"], PROXIES)

    def test_aliexpress_http_all_paths(self):
        paths = ((AliExpressCollector, "fetch"), (AliExpressRUCollector, "fetch"),
                 (AliExpressRUCollector, "_fetch_search_api"),
                 (AliExpressRUCollector, "_fetch_wholesale_html"),
                 (AliExpressRUCollector, "collect_product"))
        for cls, method in paths:
            with self.subTest(source=cls.source, method=method):
                collector = self.collector(cls)
                collector._pw_failed = True
                collector.parse_page = MagicMock(return_value={"fields": {"title": {"value": "Test GPU"}}, "price_candidates": []})
                args = ("123", "https://www.aliexpress.com/item/123.html") if method == "collect_product" else ("offline",)
                with patch("curl_cffi.requests.get", return_value=self.response) as get:
                    getattr(collector, method)(*args)
                self.assertTrue(get.called)
                for call in get.call_args_list:
                    self.assertEqual(call.kwargs["proxies"], PROXIES)

    def test_browser_fetcher_launch_receives_proxy(self):
        with patch("playwright.sync_api.sync_playwright") as factory:
            pw = factory.return_value.start.return_value
            fetcher = PwFetcher(proxies=PROXIES)
            fetcher.start()
            self.assertEqual(pw.chromium.launch.call_args.kwargs["proxy"], BROWSER_PROXY)
            fetcher.stop()

    def test_aliexpress_persistent_launch_receives_proxy(self):
        for cls in (AliExpressCollector, AliExpressRUCollector):
            with self.subTest(source=cls.source), patch("playwright.sync_api.sync_playwright") as factory, \
                    patch("r52.collectors.aliexpress.PROFILE_DIR", str(self.home / "com")), \
                    patch("r52.collectors.aliexpress.PROFILE_DIR_RU", str(self.home / "ru")):
                pw = factory.return_value.start.return_value
                context = pw.chromium.launch_persistent_context.return_value
                context.pages = [MagicMock()]
                context.cookies.return_value = []
                collector = self.collector(cls)
                self.assertTrue(collector._ensure_browser())
                self.assertEqual(pw.chromium.launch_persistent_context.call_args.kwargs["proxy"], BROWSER_PROXY)
                collector.close()


class ProxyConversionTest(unittest.TestCase):
    def test_credentials_and_ipv6(self):
        self.assertEqual(playwright_proxy(PROXIES), BROWSER_PROXY)
        self.assertEqual(playwright_proxy({"https": "http://[::1]:3128"}), {"server": "http://[::1]:3128"})

    def test_none_and_socks(self):
        self.assertIsNone(playwright_proxy(None))
        self.assertIsNone(playwright_proxy({}))
        self.assertEqual(playwright_proxy({"https": "socks5://127.0.0.1:1080"}), {"server": "socks5://127.0.0.1:1080"})

    def test_invalid_proxy_error_does_not_expose_secret(self):
        for url in ("http://REDACTED@host:bad", "ftp://host", "http://", "http://host/path"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "^INVALID_BROWSER_PROXY$"):
                playwright_proxy({"https": url})


class LocalProxyTransportTest(unittest.TestCase):
    def test_requests_and_cffi_use_local_proxy_without_external_dns(self):
        # The server is a local stand-in for a proxy, not a marketplace fixture.
        import requests
        from curl_cffi import requests as cffi_requests
        paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                payload = b'{"transport":"local-proxy"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        proxies = {"http": f"http://127.0.0.1:{server.server_port}"}
        try:
            for client in (requests, cffi_requests):
                with self.subTest(client=client.__name__):
                    result = client.get("http://r52-proxy-test.invalid/probe", proxies=proxies, timeout=5)
                    self.assertEqual(result.json(), {"transport": "local-proxy"})
            self.assertEqual(paths, ["http://r52-proxy-test.invalid/probe"] * 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
