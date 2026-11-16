"""diag20 — точечная диагностика WB/Ozon/Avito после diag19.

WB:   двухшаговый (cookie → web_api v2/v4), basket price.json
Ozon: requests verify=False → composer, curl_cffi → HTML
Avito: дамп 439-страницы, разные URL, поиск реального ID
"""
import sys, os, json, time, logging, traceback, re, warnings
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("diag20")
TZ = timezone(timedelta(hours=10))
NOW = datetime.now(TZ).isoformat()
out = {"ts": NOW, "wb": {}, "ozon": {}, "avito": {}}

# ============================================================
# 1. WB: цена через двухшаговый подход + альтернативные эндпоинты
# ============================================================
log.info("=" * 60)
log.info("WB: price hunt (two-step + alt endpoints)")
log.info("=" * 60)
try:
    from curl_cffi import requests as cffi_req

    nm_id = 1097975275
    vol = nm_id // 100000
    part = nm_id // 1000

    # Шаг 1: получить cookie с главной
    log.info("WB step1: GET main page for cookies")
    main_resp = cffi_req.get("https://www.wildberries.ru/", impersonate="chrome", timeout=15,
                             headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    log.info("WB step1: HTTP %d, cookies=%s", main_resp.status_code, dict(main_resp.cookies))
    out["wb"]["step1_cookies"] = dict(main_resp.cookies)

    # Шаг 2: web_api v1, v2, v4 с cookie
    api_urls = [
        ("web_v1", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"),
        ("web_v2", f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={nm_id}"),
        ("web_v4", f"https://www.wildberries.ru/web/api/catalog/cards/v4/detail?nm={nm_id}"),
        ("web_search", f"https://www.wildberries.ru/web/api/catalog/search/v1?query=iphone+17+pro+max&nm={nm_id}"),
    ]
    for label, url in api_urls:
        try:
            log.info("WB step2 [%s]: GET %s", label, url[:80])
            r = cffi_req.get(url, impersonate="chrome", timeout=15,
                             headers={
                                 "Accept": "application/json, text/plain, */*",
                                 "Accept-Language": "ru-RU,ru;q=0.9",
                                 "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                                 "Origin": "https://www.wildberries.ru",
                             })
            log.info("WB [%s]: HTTP %d, len=%d", label, r.status_code, len(r.content))
            out["wb"][label] = {"status": r.status_code, "len": len(r.content)}
            if r.status_code == 200:
                try:
                    d = r.json()
                    out["wb"][label]["keys"] = list(d.keys())[:20] if isinstance(d, dict) else type(d).__name__
                    out["wb"][label]["sample"] = json.dumps(d, ensure_ascii=False)[:600]
                    # Ищем цены
                    def find_prices(obj, path=""):
                        results = []
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if k in ("price", "priceU", "priceD", "basic", "product", "card", "old", "crossed") and isinstance(v, (int, float)) and v > 100:
                                    results.append({"path": f"{path}.{k}", "val": v})
                                find_prices(v, f"{path}.{k}" if path else k)
                        elif isinstance(obj, list):
                            for i, v in enumerate(obj[:10]):
                                find_prices(v, f"{path}[{i}]")
                        return results
                    prices = find_prices(d)
                    out["wb"][label]["prices_found"] = prices[:10]
                    log.info("WB [%s]: prices=%s", label, prices[:5])
                except Exception as e2:
                    out["wb"][label]["parse_err"] = str(e2)
                    out["wb"][label]["body"] = r.text[:300]
            elif r.status_code in (403, 498, 429):
                out["wb"][label]["body"] = r.text[:300]
            time.sleep(2)
        except Exception as e2:
            log.warning("WB [%s] error: %s", label, e2)
            out["wb"][label] = {"error": str(e2)}

    # Шаг 3: basket CDN — может быть отдельный price-файл
    log.info("WB step3: basket CDN price paths")
    basket_paths = [
        f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json",
        f"/vol{vol}/part{part}/{nm_id}/info/ru/price.json",
        f"/vol{vol}/part{part}/{nm_id}/price/ru/price.json",
    ]
    for bp in basket_paths:
        try:
            url = f"https://basket-42.wbbasket.ru{bp}"
            log.info("WB basket: GET %s", url)
            r = cffi_req.get(url, impersonate="chrome", timeout=10,
                             headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
            log.info("WB basket: HTTP %d, len=%d", r.status_code, len(r.content))
            label = bp.split("/")[-1]
            out["wb"][f"basket_{label}"] = {"status": r.status_code, "len": len(r.content)}
            if r.status_code == 200:
                d = r.json()
                out["wb"][f"basket_{label}"]["keys"] = list(d.keys())[:20] if isinstance(d, dict) else str(d)[:200]
                out["wb"][f"basket_{label}"]["sample"] = json.dumps(d, ensure_ascii=False)[:400]
        except Exception as e2:
            out["wb"][f"basket_{bp.split('/')[-1]}"] = {"error": str(e2)}
        time.sleep(1)

    # Шаг 4: card.wb.ru с cookie из шага 1
    log.info("WB step4: card.wb.ru with cookies")
    try:
        r = cffi_req.get(
            f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
            impersonate="chrome", timeout=15,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Referer": "https://www.wildberries.ru/",
                "Origin": "https://www.wildberries.ru",
            })
        log.info("WB card.wb v2: HTTP %d, len=%d", r.status_code, len(r.content))
        out["wb"]["card_wb_v2"] = {"status": r.status_code, "len": len(r.content)}
        if r.status_code == 200:
            d = r.json()
            prods = d.get("data", {}).get("items") or d.get("products", [])
            if prods:
                p = prods[0]
                out["wb"]["card_wb_v2"]["name"] = p.get("name")
                out["wb"]["card_wb_v2"]["sizes_count"] = len(p.get("sizes", []))
                if p.get("sizes"):
                    sz = p["sizes"][0]
                    out["wb"]["card_wb_v2"]["size0_price"] = sz.get("price")
                    out["wb"]["card_wb_v2"]["size0_qty"] = sz.get("qty")
    except Exception as e2:
        out["wb"]["card_wb_v2"] = {"error": str(e2)}

except Exception as e:
    log.exception("WB fatal: %s", e)
    out["wb"]["fatal"] = str(e)

# ============================================================
# 2. OZON: fix SSL + composer + curl_cffi HTML
# ============================================================
log.info("=" * 60)
log.info("OZON: SSL fix + composer + cffi HTML")
log.info("=" * 60)
try:
    import requests as req

    session = req.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.ozon.ru/",
    })

    # A: Composer API с verify=False
    log.info("OZON A: composer-api verify=False")
    comp_urls = [
        ("comp_v2", f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/2067947177/"),
        ("comp_v1", f"https://api.ozon.ru/composer-api.bx/page/json/v1?url=/product/2067947177/"),
        ("pdp_api", f"https://www.ozon.ru/api/entrypoint/pdp/2067947177"),
    ]
    for label, url in comp_urls:
        try:
            log.info("OZON [%s]: GET %s (verify=False)", label, url[:80])
            r = session.get(url, timeout=(5, 15), verify=False)
            log.info("OZON [%s]: HTTP %d, len=%d", label, r.status_code, len(r.content))
            out["ozon"][label] = {"status": r.status_code, "len": len(r.content)}
            if r.status_code == 200:
                d = r.json()
                out["ozon"][label]["keys"] = list(d.keys())[:20] if isinstance(d, dict) else type(d).__name__
                out["ozon"][label]["sample"] = json.dumps(d, ensure_ascii=False)[:800]
                # Ищем title и price
                def deep_find(obj, keys, path=""):
                    if isinstance(obj, dict):
                        for k in keys:
                            if k in obj and obj[k]:
                                return {"path": f"{path}.{k}", "val": str(obj[k])[:200]}
                        for k, v in obj.items():
                            r2 = deep_find(v, keys, f"{path}.{k}")
                            if r2:
                                return r2
                    elif isinstance(obj, list):
                        for i, v in enumerate(obj[:5]):
                            r2 = deep_find(v, keys, f"{path}[{i}]")
                            if r2:
                                return r2
                    return None
                t = deep_find(d, ["title", "name", "seoTitle"])
                p = deep_find(d, ["price", "priceInfo", "currentPrice", "basicPrice"])
                out["ozon"][label]["title_found"] = t
                out["ozon"][label]["price_found"] = p
                log.info("OZON [%s]: title=%s price=%s", label,
                         t.get("val", "?")[:40] if t else "?",
                         p.get("val", "?")[:40] if p else "?")
            elif r.status_code in (403, 429):
                out["ozon"][label]["body"] = r.text[:300]
            time.sleep(2)
        except Exception as e2:
            log.warning("OZON [%s] error: %s", label, e2)
            out["ozon"][label] = {"error": str(e2)}

    # B: curl_cffi на HTML (desktop)
    log.info("OZON B: curl_cffi HTML")
    try:
        from curl_cffi import requests as cffi_req
        r = cffi_req.get("https://www.ozon.ru/product/2067947177/", impersonate="chrome",
                         timeout=20, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })
        log.info("OZON cffi HTML: HTTP %d, len=%d", r.status_code, len(r.content))
        out["ozon"]["cffi_html"] = {"status": r.status_code, "len": len(r.content)}
        if r.status_code == 200 and len(r.text) > 1000:
            title_m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', r.text, re.I)
            price_m = re.search(r'"price"\s*:\s*"?(\d+)', r.text)
            data_w = re.findall(r'data-widget=["\']([^"\']+)', r.text)
            out["ozon"]["cffi_html"]["og_title"] = title_m.group(1)[:100] if title_m else None
            out["ozon"]["cffi_html"]["price_raw"] = price_m.group(1) if price_m else None
            out["ozon"]["cffi_html"]["widgets"] = data_w[:10]
            log.info("OZON cffi: title=%s price=%s widgets=%s",
                     out["ozon"]["cffi_html"].get("og_title", "?")[:50],
                     out["ozon"]["cffi_html"].get("price_raw"),
                     data_w[:5])
        elif r.status_code == 403:
            # Дамп 403-страницы — может быть useful
            out["ozon"]["cffi_html"]["body_403"] = r.text[:500]
    except Exception as e2:
        out["ozon"]["cffi_html"] = {"error": str(e2)}

    # C: curl_cffi mobile
    try:
        r = cffi_req.get("https://m.ozon.ru/product/2067947177/", impersonate="chrome",
                         timeout=20, headers={
            "Accept": "text/html",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        })
        log.info("OZON cffi mobile: HTTP %d, len=%d", r.status_code, len(r.content))
        out["ozon"]["cffi_mobile"] = {"status": r.status_code, "len": len(r.content)}
        if r.status_code == 200:
            title_m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', r.text, re.I)
            out["ozon"]["cffi_mobile"]["og_title"] = title_m.group(1)[:100] if title_m else None
    except Exception as e2:
        out["ozon"]["cffi_mobile"] = {"error": str(e2)}

except Exception as e:
    log.exception("OZON fatal: %s", e)
    out["ozon"]["fatal"] = str(e)

# ============================================================
# 3. AVITO: дамп 439 + реальный ID + разные подходы
# ============================================================
log.info("=" * 60)
log.info("AVITO: 439 dump + real ID hunt")
log.info("=" * 60)
try:
    from curl_cffi import requests as cffi_req

    # A: Дамп 439-страницы (что там?)
    log.info("AVITO A: dump 439 page")
    r = cffi_req.get("https://www.avito.ru/moskva/smartfony-i-telefony", impersonate="chrome",
                     timeout=20, headers={
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9",
    })
    log.info("AVITO 439: HTTP %d, len=%d", r.status_code, len(r.content))
    out["avito"]["439_dump"] = {"status": r.status_code, "len": len(r.content)}
    if r.status_code == 439:
        # Ищем в 439-странице: title, links, JSON
        html = r.text
        out["avito"]["439_dump"]["title"] = re.search(r'<title[^>]*>([^<]+)', html)
        out["avito"]["439_dump"]["title"] = out["avito"]["439_dump"]["title"].group(1) if out["avito"]["439_dump"]["title"] else None
        # data-state / __PRELOADED_STATE__
        state_m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
        out["avito"]["439_dump"]["has_state"] = bool(state_m)
        # Links на объявления
        ad_links = re.findall(r'href="(/[^"]*?/(\d{9,15})[^"]*)"', html)
        out["avito"]["439_dump"]["ad_links"] = ad_links[:10]
        # data-атрибуты
        data_items = re.findall(r'data-item-id=["\'](\d+)', html)
        out["avito"]["439_dump"]["data_item_ids"] = data_items[:10]
        # og: meta
        og = re.findall(r'<meta[^>]*property=["\']og:([^"\']+)["\'][^>]*content=["\']([^"\']+)', html)
        out["avito"]["439_dump"]["og"] = og[:5]
        # body snippet
        out["avito"]["439_dump"]["body_snippet"] = html[1000:2000]
        log.info("AVITO 439: title=%s state=%s links=%d items=%d",
                 out["avito"]["439_dump"]["title"], out["avito"]["439_dump"]["has_state"],
                 len(ad_links), len(data_items))

    # B: Разные category URL (правильные slug'и)
    log.info("AVITO B: correct category URLs")
    cat_urls = [
        ("smartfony", "https://www.avito.ru/moskva/smartfony"),
        ("telefony", "https://www.avito.ru/moskva/telefony"),
        ("gadzety", "https://www.avito.ru/moskva/gadzety"),
        ("search_api", "https://www.avito.ru/moskva/search?query=iphone"),
    ]
    avito_id = None
    for label, url in cat_urls:
        try:
            log.info("AVITO cat [%s]: GET %s", label, url)
            r = cffi_req.get(url, impersonate="chrome", timeout=20, headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
            })
            log.info("AVITO cat [%s]: HTTP %d, len=%d", label, r.status_code, len(r.content))
            out["avito"][f"cat_{label}"] = {"status": r.status_code, "len": len(r.content)}
            if r.status_code == 200 and len(r.text) > 2000:
                # Ищем ID
                ids = re.findall(r'href="[^"]*?/(\d{9,15})/?"', r.text)
                ids += re.findall(r'data-item-id=["\'](\d{9,15})', r.text)
                ids = list(set(ids))
                out["avito"][f"cat_{label}"]["ids"] = ids[:15]
                log.info("AVITO cat [%s]: IDs=%s", label, ids[:5])
                if ids and not avito_id:
                    avito_id = ids[0]
            time.sleep(3)
        except Exception as e2:
            out["avito"][f"cat_{label}"] = {"error": str(e2)}

    # C: Если нашли ID — пробуем карточку
    if avito_id:
        log.info("AVITO C: try ad %s", avito_id)
        ad_url = f"https://www.avito.ru/ad/{avito_id}"
        try:
            r = cffi_req.get(ad_url, impersonate="chrome", timeout=20, headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
            })
            log.info("AVITO ad: HTTP %d, len=%d", r.status_code, len(r.content))
            out["avito"]["ad_test"] = {"id": avito_id, "status": r.status_code, "len": len(r.content)}
            if r.status_code == 200 and len(r.text) > 1000:
                title_m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', r.text, re.I)
                price_m = re.search(r'"price"\s*:\s*"?([\d.]+)', r.text)
                jsonld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', r.text, re.DOTALL)
                out["avito"]["ad_test"]["og_title"] = title_m.group(1)[:100] if title_m else None
                out["avito"]["ad_test"]["price"] = price_m.group(1) if price_m else None
                out["avito"]["ad_test"]["jsonld_count"] = len(jsonld)
                if jsonld:
                    try:
                        ld = json.loads(jsonld[0])
                        out["avito"]["ad_test"]["jsonld"] = json.dumps(ld, ensure_ascii=False)[:500]
                    except:
                        out["avito"]["ad_test"]["jsonld_raw"] = jsonld[0][:300]
                log.info("AVITO ad: title=%s price=%s jsonld=%d",
                         out["avito"]["ad_test"].get("og_title", "?")[:50],
                         out["avito"]["ad_test"].get("price"),
                         len(jsonld))
        except Exception as e2:
            out["avito"]["ad_test"] = {"id": avito_id, "error": str(e2)}
    else:
        log.info("AVITO C: no ID found from categories")
        out["avito"]["ad_test"] = {"note": "no ID found"}

    # D: Avito API (JSON)
    log.info("AVITO D: API search")
    try:
        api_url = "https://www.avito.ru/api/v1/search?query=iphone+15&limit=5"
        r = cffi_req.get(api_url, impersonate="chrome", timeout=15, headers={
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })
        log.info("AVITO API: HTTP %d, len=%d", r.status_code, len(r.content))
        out["avito"]["api"] = {"status": r.status_code, "len": len(r.content)}
        if r.status_code == 200:
            d = r.json()
            out["avito"]["api"]["sample"] = json.dumps(d, ensure_ascii=False)[:500]
    except Exception as e2:
        out["avito"]["api"] = {"error": str(e2)}

except Exception as e:
    log.exception("AVITO fatal: %s", e)
    out["avito"]["fatal"] = str(e)

# ============================================================
# SAVE
# ============================================================
out_path = os.path.join(HERE, "diag20_result.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2, default=str)
log.info("Result → %s", out_path)
log.info("DONE")
