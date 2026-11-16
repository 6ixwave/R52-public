"""Диагностика: WB card.json keys, WB price endpoints, Ozon SSL, Avito URL."""
import json, sys, time, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag")

# ============================================================
# 1. WB: card.json — выдать ВСЕ ключи и иерархию
# ============================================================
def diag_wb():
    log.info("=== WB DIAG ===")
    nm_id = 1097975275
    vol = nm_id // 100000
    part = nm_id // 1000
    path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    host = "basket-42.wbbasket.ru"  # из кэша E2E
    url = f"https://{host}{path}"
    log.info("WB card.json: %s", url)

    try:
        from curl_cffi import requests as cffi_req
        resp = cffi_req.get(url, impersonate="chrome", timeout=15,
                            headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
        if resp.status_code != 200:
            log.warning("WB card.json HTTP %d", resp.status_code)
            return
        data = resp.json()
        # Печатать все ключи верхнего уровня
        log.info("WB card.json TOP-LEVEL keys (%d): %s", len(data), list(data.keys()))
        # selling
        selling = data.get("selling")
        if selling:
            log.info("WB selling keys: %s", list(selling.keys()) if isinstance(selling, dict) else type(selling))
            if isinstance(selling, dict):
                for k, v in selling.items():
                    if isinstance(v, (int, float)) and v > 0:
                        log.info("  selling.%s = %s", k, v)
        # sizes
        sizes = data.get("sizes", [])
        if sizes:
            log.info("WB sizes: %d items", len(sizes))
            for i, s in enumerate(sizes[:3]):
                if isinstance(s, dict):
                    log.info("  size[%d] keys: %s", i, list(s.keys()))
                    pr = s.get("price")
                    if pr:
                        log.info("  size[%d].price: %s", i, pr)
        # price на верхнем уровне
        for pk in ("price", "priceU", "priceD", "oldPrice", "salePriceU", "salePriceD",
                    "priceProduct", "priceCard", "priceWallet", "priceBankCard"):
            pv = data.get(pk)
            if pv is not None:
                log.info("WB top.%s = %s", pk, pv)
        # sale
        sale = data.get("sale")
        if sale:
            log.info("WB sale: %s", sale)
        # gallery / media
        media = data.get("media")
        if media:
            log.info("WB media keys: %s", list(media.keys()) if isinstance(media, dict) else type(media))
        # FULL dump (truncated)
        dump = json.dumps(data, ensure_ascii=False, indent=1)
        log.info("WB card.json FULL (%d chars):\n%s", len(dump), dump[:6000])
    except Exception as e:
        log.error("WB card.json error: %s", e)

    # --- WB Price: новые эндпоинты ---
    log.info("WB PRICE ENDPOINTS:")
    price_urls = [
        # basket price (иногда работает отдельно от card.wb.ru)
        f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/price.json",
        # WB mobile API
        f"https://app.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}",
        # WB search API (другой формат)
        f"https://search.wb.ru/exactmatch/ru/common/v4/search?appType=1&curr=rub&dest=-1257786&nm={nm_id}",
        # WB card v2 с другим dest (Владивосток = -1257786? или регион 25?)
        f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=25&spp=30&nm={nm_id}",
        # WB basket-CDN price (иногда на том же хосте)
        f"https://{host}/vol{vol}/part{part}/{nm_id}/images/S800/S800.jpg",  # check if CDN alive
    ]
    for i, pu in enumerate(price_urls):
        try:
            time.sleep(1.5)
            resp = cffi_req.get(pu, impersonate="chrome", timeout=12,
                                headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
            ct = resp.headers.get("content-type", "")
            body = resp.text[:500] if resp.status_code == 200 else resp.text[:200]
            log.info("WB price[%d] %s -> %d (%s) body[:200]=%s",
                     i, pu.split("?")[0].split("/")[-1], resp.status_code, ct, body[:200])
        except Exception as e:
            log.info("WB price[%d] error: %s", i, e)

# ============================================================
# 2. OZON: SSL fix + headers
# ============================================================
def diag_ozon():
    log.info("=== OZON DIAG ===")
    lid = "2067947177"
    url = f"https://www.ozon.ru/product/{lid}/"

    # Try 1: curl_cffi with verify=False
    try:
        from curl_cffi import requests as cffi_req
        resp = cffi_req.get(url, impersonate="chrome", timeout=15, verify=False,
                            headers={
                                "Accept": "text/html,application/xhtml+xml",
                                "Accept-Language": "ru-RU,ru;q=0.9",
                                "x-o3-app": "1",
                                "x-o3-app-version": "1.0",
                            })
        log.info("OZON cffi(no-verify) -> %d, len=%d", resp.status_code, len(resp.text))
        if resp.status_code == 200:
            # Extract price from HTML
            import re
            prices = re.findall(r'[\d\s]{5,}\s*(?:₽|руб)', resp.text)
            log.info("OZON visible prices: %s", prices[:5])
            og_title = re.search(r'og:title[^>]*content="([^"]+)', resp.text)
            log.info("OZON og:title: %s", og_title.group(1)[:60] if og_title else "None")
    except Exception as e:
        log.error("OZON cffi error: %s", e)

    # Try 2: Composer API with proper headers
    composer_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{lid}/"
    try:
        time.sleep(2)
        resp = cffi_req.get(composer_url, impersonate="chrome", timeout=15, verify=False,
                            headers={
                                "Accept": "application/json",
                                "Accept-Language": "ru-RU,ru;q=0.9",
                                "Referer": "https://www.ozon.ru/",
                                "x-o3-app": "1",
                                "x-o3-app-version": "1.0",
                            })
        log.info("OZON composer -> %d", resp.status_code)
        if resp.status_code == 200:
            d = resp.json()
            log.info("OZON composer keys: %s", list(d.keys())[:10])
            # Look for price
            def find_price(obj, path=""):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k.lower() in ("price", "current", "basic", "old", "value") and isinstance(v, (int, float)) and v > 100:
                            log.info("  OZON price found: %s.%s = %s", path, k, v)
                        find_price(v, f"{path}.{k}")
                elif isinstance(obj, list):
                    for i, v in enumerate(obj[:20]):
                        find_price(v, f"{path}[{i}]")
            find_price(d)
    except Exception as e:
        log.error("OZON composer error: %s", e)

    # Try 3: mobile API
    mob_url = f"https://api.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{lid}/"
    try:
        time.sleep(2)
        resp = cffi_req.get(mob_url, impersonate="chrome", timeout=12, verify=False,
                            headers={"Accept": "application/json", "Referer": "https://www.ozon.ru/"})
        log.info("OZON mobile-api -> %d", resp.status_code)
        if resp.status_code == 200:
            log.info("OZON mobile body[:300]=%s", resp.text[:300])
    except Exception as e:
        log.error("OZON mobile error: %s", e)

# ============================================================
# 3. AVITO: check listing + alternative URLs
# ============================================================
def diag_avito():
    log.info("=== AVITO DIAG ===")
    lid = "1045231001"
    urls = [
        f"https://www.avito.ru/ad/{lid}",
        f"https://m.avito.ru/ad/{lid}",
        f"https://www.avito.ru/vladivostok/ad/{lid}",
        f"https://avito.ru/ad/{lid}",
    ]
    try:
        from curl_cffi import requests as cffi_req
    except ImportError:
        import requests as cffi_req
        cffi_req = type("S", (), {"get": staticmethod(lambda *a, **k: requests.get(*a, **k))})()

    import requests
    for u in urls:
        try:
            time.sleep(2)
            resp = requests.get(u, timeout=15, headers={
                "Accept": "text/html",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            })
            log.info("AVITO %s -> %d len=%d", u.split("/")[2], resp.status_code, len(resp.text))
            if resp.status_code == 200:
                import re
                t = re.search(r'<title[^>]*>([^<]+)', resp.text)
                log.info("  title: %s", t.group(1)[:60] if t else "?")
                p = re.findall(r'[\d\s]{4,}\s*(?:₽|руб)', resp.text)
                log.info("  prices: %s", p[:3])
        except Exception as e:
            log.info("AVITO %s error: %s", u, e)

# ============================================================
if __name__ == "__main__":
    t0 = time.time()
    diag_wb()
    diag_ozon()
    diag_avito()
    log.info("DIAG DONE in %.1fs", time.time() - t0)
    print("DIAG_DONE")
