"""Тесты сборщиков маркетплейсов.

Запуск:
    cd C:\\Users\\aleks\\Downloads\\R52-2.12\\R52-2.12
    python -m pytest r52/collectors/test_collectors.py -v
    или
    python -m r52.collectors.test_collectors
"""

import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Добавляем корень проекта в sys.path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from r52.collectors.base import BaseCollector
from r52.collectors.wb import WBCollector
from r52.collectors.ozon import OzonCollector
from r52.collectors.avito import AvitoCollector
from r52.collectors.farpost import FarpostCollector
from r52.collectors.proxy_pool import ProxyPool


# === Тесты BaseCollector (статические методы) ===

class TestBaseCollector(unittest.TestCase):

    def test_extract_json_ld(self):
        html = '''
        <html><head>
        <script type="application/ld+json">{"@type": "Product", "name": "Test"}</script>
        <script type="application/ld+json">[{"@type": "Offer", "price": "100"}]</script>
        </head><body></body></html>
        '''
        blocks = BaseCollector.extract_json_ld(html)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["@type"], "Product")
        self.assertEqual(blocks[1]["@type"], "Offer")

    def test_find_product_jsonld(self):
        blocks = [
            {"@type": "WebSite", "name": "Test"},
            {"@type": "Product", "name": "Widget", "offers": {"price": "99"}},
        ]
        product = BaseCollector.find_product_jsonld(blocks)
        self.assertIsNotNone(product)
        self.assertEqual(product["name"], "Widget")

    def test_find_product_jsonld_in_graph(self):
        blocks = [
            {"@graph": [
                {"@type": "Organization"},
                {"@type": "Product", "name": "Graph Product"},
            ]},
        ]
        product = BaseCollector.find_product_jsonld(blocks)
        self.assertIsNotNone(product)
        self.assertEqual(product["name"], "Graph Product")

    def test_parse_price_text(self):
        result = BaseCollector.parse_price_text("1 234 ₽")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 123400)  # копейки
        self.assertEqual(result[1], "RUB")

    def test_parse_price_text_decimal(self):
        result = BaseCollector.parse_price_text("999,99 руб")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 99999)

    def test_parse_price_text_no_match(self):
        result = BaseCollector.parse_price_text("hello world")
        self.assertIsNone(result)

    def test_safe_media_url_wb(self):
        url = "https://basket-1.wbbasket.ru/vol1/part1/123/images/S800.jpg"
        self.assertEqual(BaseCollector.safe_media_url(url, "WB"), url)

    def test_safe_media_url_reject_foreign(self):
        url = "https://example.com/image.jpg"
        self.assertIsNone(BaseCollector.safe_media_url(url, "WB"))

    def test_safe_media_url_ozon(self):
        url = "https://ir-220.ozoncdn.net/12345/67890.jpg"
        self.assertEqual(BaseCollector.safe_media_url(url, "OZON"), url)

    def test_safe_media_url_avito(self):
        url = "https://avatars.mmedia.avito.st/12345/photo.jpg"
        self.assertEqual(BaseCollector.safe_media_url(url, "AVITO"), url)

    def test_safe_media_url_farpost(self):
        url = "https://www.farpost.ru/upload/items/123.jpg"
        self.assertEqual(BaseCollector.safe_media_url(url, "FARPOST"), url)

    def test_safe_media_url_http_reject(self):
        url = "http://basket-1.wbbasket.ru/vol1/part1/123/images/S800.jpg"
        self.assertIsNone(BaseCollector.safe_media_url(url, "WB"))


# === Тесты WBCollector ===

class TestWBCollector(unittest.TestCase):

    def setUp(self):
        self.collector = WBCollector(use_proxies=False)

    def test_build_url(self):
        url = self.collector.build_url("12345")
        self.assertEqual(url, "https://www.wildberries.ru/catalog/12345/detail.aspx")

    def test_parse_page_jsonld(self):
        html = '''
        <html><head>
        <meta property="og:title" content="Тестовый товар WB" />
        <meta property="og:image" content="https://basket-1.wbbasket.ru/vol1/part1/123/images/S800.jpg" />
        <script type="application/ld+json">
        {
            "@type": "Product",
            "name": "Тестовый товар WB",
            "image": "https://basket-1.wbbasket.ru/vol1/part1/123/images/S800.jpg",
            "description": "Описание товара",
            "offers": {
                "@type": "Offer",
                "price": "1499.00",
                "priceCurrency": "RUB",
                "availability": "https://schema.org/InStock"
            }
        }
        </script>
        </head><body>
        <img src="https://basket-2.wbbasket.ru/vol1/part1/123/images/S400.jpg" />
        <img src="https://basket-3.wbbasket.ru/vol1/part1/123/images/S400.jpg" />
        <video src="https://basket-1.wbbasket.ru/vol1/part1/123/video.mp4"></video>
        </body></html>
        '''
        mock_resp = MagicMock()
        mock_resp.text = html

        result = self.collector.parse_page(mock_resp, "12345", "https://www.wildberries.ru/catalog/12345/detail.aspx", "")
        self.assertIsNotNone(result)

        # Title
        self.assertEqual(result["fields"]["title"]["value"], "Тестовый товар WB")

        # Price
        prices = result["price_candidates"]
        self.assertTrue(len(prices) > 0)
        self.assertTrue(any(p["amount_minor"] == 149900 for p in prices))

        # Media
        media = result["media"]["items"]
        images = [m for m in media if m["type"] == "image"]
        videos = [m for m in media if m["type"] == "video"]
        self.assertTrue(len(images) >= 2)
        self.assertTrue(len(videos) >= 1)

        # Availability
        self.assertEqual(result["availability"], "IN_STOCK")

    def test_parse_page_html_fallback(self):
        html = '''
        <html><head>
        <title>Товар - Wildberries</title>
        <meta property="og:price:amount" content="999.50" />
        </head><body>
        <span class="price">999 ₽</span>
        <img src="https://basket-1.wbbasket.ru/vol1/part1/456/images/S600.jpg" />
        </body></html>
        '''
        mock_resp = MagicMock()
        mock_resp.text = html

        result = self.collector.parse_page(mock_resp, "456", "https://www.wildberries.ru/catalog/456/detail.aspx", "Fallback")
        self.assertIsNotNone(result)
        self.assertTrue(len(result["price_candidates"]) > 0)
        self.assertTrue(len(result["media"]["items"]) > 0)

    def test_normalize_wb_image_url(self):
        url = "https://basket-5.wbbasket.ru/vol2/part3/789/images/S100.jpg"
        normalized = WBCollector._normalize_wb_image_url(url)
        self.assertIn("S800", normalized)
        self.assertNotIn("S100", normalized)


# === Тесты OzonCollector ===

class TestOzonCollector(unittest.TestCase):

    def setUp(self):
        self.collector = OzonCollector(use_proxies=False)

    def test_build_url(self):
        url = self.collector.build_url("67890")
        self.assertEqual(url, "https://www.ozon.ru/product/67890/")

    def test_parse_page_jsonld(self):
        html = '''
        <html><head>
        <meta property="og:title" content="Товар Ozon" />
        <meta property="og:image" content="https://ir-220.ozoncdn.net/111/222.jpg" />
        <script type="application/ld+json">
        {
            "@type": "Product",
            "name": "Товар Ozon",
            "image": ["https://ir-220.ozoncdn.net/111/222.jpg", "https://ir-220.ozoncdn.net/111/333.jpg"],
            "description": "Описание Ozon",
            "brand": {"@type": "Brand", "name": "TestBrand"},
            "sku": "SKU-001",
            "offers": {
                "@type": "Offer",
                "price": "2999.00",
                "priceCurrency": "RUB",
                "availability": "https://schema.org/InStock"
            }
        }
        </script>
        </head><body>
        <img src="https://ir-220.ozoncdn.net/111/444.jpg" />
        <video src="https://ir-220.ozoncdn.net/111/video.mp4"></video>
        </body></html>
        '''
        mock_resp = MagicMock()
        mock_resp.text = html

        result = self.collector.parse_page(mock_resp, "67890", "https://www.ozon.ru/product/67890/", "")
        self.assertIsNotNone(result)
        self.assertEqual(result["fields"]["title"]["value"], "Товар Ozon")
        self.assertTrue(any(p["amount_minor"] == 299900 for p in result["price_candidates"]))
        self.assertEqual(result["fields"]["brand"]["value"], "TestBrand")
        self.assertEqual(result["fields"]["model"]["value"], "SKU-001")
        media = result["media"]["items"]
        self.assertTrue(len(media) >= 3)  # 2 из JSON-LD + 1 из HTML
        self.assertEqual(result["availability"], "IN_STOCK")


# === Тесты AvitoCollector ===

class TestAvitoCollector(unittest.TestCase):

    def setUp(self):
        self.collector = AvitoCollector(use_proxies=False)

    def test_build_url(self):
        url = self.collector.build_url("1122334455")
        self.assertEqual(url, "https://www.avito.ru/ad/1122334455")

    def test_parse_page_jsonld(self):
        html = '''
        <html><head>
        <meta property="og:title" content="Объявление Avito" />
        <meta property="og:image" content="https://avatars.mmedia.avito.st/555/photo.jpg" />
        <script type="application/ld+json">
        {
            "@type": "Product",
            "name": "Объявление Avito",
            "image": "https://avatars.mmedia.avito.st/555/photo.jpg",
            "description": "Описание Avito",
            "offers": {
                "@type": "Offer",
                "price": "5000.00",
                "priceCurrency": "RUB",
                "availability": "https://schema.org/InStock"
            }
        }
        </script>
        </head><body>
        <img src="https://avatars.mmedia.avito.st/555/photo2.jpg" />
        </body></html>
        '''
        mock_resp = MagicMock()
        mock_resp.text = html

        result = self.collector.parse_page(mock_resp, "1122334455", "https://www.avito.ru/ad/1122334455", "")
        self.assertIsNotNone(result)
        self.assertEqual(result["fields"]["title"]["value"], "Объявление Avito")
        self.assertTrue(any(p["amount_minor"] == 500000 for p in result["price_candidates"]))
        self.assertTrue(len(result["media"]["items"]) >= 2)

    def test_captcha_detection(self):
        html = '''
        <html><head><title>Avito</title></head><body>
        <div id="smartcaptcha-container"></div>
        <script src="https://smartcaptcha.ru/api.js"></script>
        </body></html>
        '''
        mock_resp = MagicMock()
        mock_resp.text = html
        result = self.collector.parse_page(mock_resp, "999", "https://www.avito.ru/ad/999", "")
        self.assertIsNone(result)  # CAPTCHA → None


# === Тесты FarpostCollector ===

class TestFarpostCollector(unittest.TestCase):

    def setUp(self):
        self.collector = FarpostCollector(use_proxies=False)

    def test_build_url(self):
        url = self.collector.build_url("778899")
        self.assertEqual(url, "https://www.farpost.ru/vladivostok/778899.html")

    def test_parse_page_html(self):
        html = '''
        <html><head>
        <title>Товар Farpost - Farpost</title>
        <meta property="og:image" content="https://www.farpost.ru/upload/items/778.jpg" />
        <meta property="og:price:amount" content="1500.00" />
        </head><body>
        <div class="price">1 500 ₽</div>
        <img src="https://www.farpost.ru/upload/items/778a.jpg" />
        <img src="/upload/items/778b.jpg" />
        <div class="description">Описание товара Farpost. Хороший товар.</div>
        </body></html>
        '''
        mock_resp = MagicMock()
        mock_resp.text = html

        result = self.collector.parse_page(mock_resp, "778899", "https://www.farpost.ru/item/778899", "")
        self.assertIsNotNone(result)
        self.assertEqual(result["fields"]["title"]["value"], "Товар Farpost")
        self.assertTrue(len(result["price_candidates"]) > 0)
        media = result["media"]["items"]
        self.assertTrue(len(media) >= 2)


# === Тесты ProxyPool ===

class TestProxyPool(unittest.TestCase):
    """Тесты пула прокси. Используем temp-каталог, чтобы не подхватывать
    реальный proxies.json из AppData (1000 прокси)."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="r52_pool_test_")
        self._data_dir = Path(self._tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _pool(self, **kw):
        return ProxyPool(data_dir=self._data_dir, use_proxies=True, **kw)

    def test_empty_pool_direct(self):
        pool = self._pool()
        proxy = pool.get_proxy()
        self.assertIsNone(proxy)  # Нет прокси → direct

    def test_pool_with_proxies(self):
        pool = self._pool()
        pool.add_proxies(["http://1.2.3.4:8080", "socks5://5.6.7.8:1080"])
        proxy = pool.get_proxy()
        self.assertIsNotNone(proxy)
        self.assertIn("http", proxy)

    def test_pool_rotation(self):
        pool = self._pool()
        pool.add_proxies(["http://1.1.1.1:80", "http://2.2.2.2:80", "http://3.3.3.3:80"])
        p1 = pool.get_proxy()
        p2 = pool.get_proxy()
        p3 = pool.get_proxy()
        # Ротация: не все одинаковые
        urls = {p1["http"], p2["http"], p3["http"]}
        self.assertTrue(len(urls) >= 2)

    def test_pool_mark_failure(self):
        pool = self._pool()
        pool.add_proxies(["http://1.1.1.1:80"])
        for _ in range(5):
            pool.get_proxy()
            pool.mark_failure()
        # После 5 fail → мёртвый
        proxy = pool.get_proxy()
        self.assertIsNone(proxy)

    def test_pool_stats(self):
        pool = self._pool()
        pool.add_proxies(["http://1.1.1.1:80", "http://2.2.2.2:80"])
        stats = pool.stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["alive"], 2)


# === Тесты интеграции (реестр) ===

class TestSourcesRegistry(unittest.TestCase):

    def test_all_collectors_registered(self):
        from r52.sources import REGISTRY, collectors_status
        status = collectors_status()
        sources = {s["source"]: s["collector"] for s in status}
        self.assertEqual(sources["WB"], "READY")
        self.assertEqual(sources["OZON"], "READY")
        self.assertEqual(sources["AVITO"], "READY")
        self.assertEqual(sources["FARPOST"], "READY")

    def test_collector_for(self):
        from r52.sources import collector_for
        self.assertIsNotNone(collector_for("WB"))
        self.assertIsNotNone(collector_for("OZON"))
        self.assertIsNotNone(collector_for("AVITO"))
        self.assertIsNotNone(collector_for("FARPOST"))
        self.assertIsNone(collector_for("UNKNOWN"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
