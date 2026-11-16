r"""probe6.py — Уточнённые стратегии после probe5.
WB: точные эндпоинты из wb.py + basket CDN price + sec-ch-ua
Ozon: two-step cookie + entrypoint с полными заголовками
Avito: поиск реального listing + fetch
Run: C:\Python315\python.exe probe6.py
"""
import sys, os, json, time, re, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("P6")

RESULTS = {}

def mark(src, label, ok, detail=""):
    key = f"{src}:{label}"
    RESULTS[key] = {"ok": ok, "detail": detail}
    log.info("[%s] %s → %s %s", src, label, "PASS" if ok else "FAIL", detail[:150])

# Полные browser-like заголовки
BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="131", "Google Chrome";v="131"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

JSON_HEADERS_FULL = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="131", "Google Chrome";v="131"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

# ============================================================
# WB: 5 стратегий цены
# ============================================================
def wb_price(nm_id=1097975275):
    log.info("=== WB PRICE (nm_id=%d) ===", nm_id)
    from curl_cffi import requests as cr

    vol = nm_id // 100000  # 10
    part = nm_id // 1000   # 1097

    # S0: Проверить что basket CDN card.json ещё работает
    basket_host = None
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        u = f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        try:
            r = cr.get(u, impersonate="chrome", timeout=8,
                       headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
            if r.status_code == 200:
                d = r.json()
                if d.get("nm_id") == nm_id:
                    basket_host = n
                    mark("WB", "basket_card", True, f"host={host} keys={len(d)}")
                    break
        except: pass
    if not basket_host:
        mark("WB", "basket_card", False, "no host found in 60 tries")
        # Всё равно пробуем price
    else:
        log.info("WB basket host: basket-%02d", basket_host)

    # S1: card.wb.ru EXACT как в wb.py (dest=-1257786)
    for ver in ["v1", "v2", "v4", "v5"]:
        try:
            u = (f"https://card.wb.ru/cards/{ver}/detail"
                 f"?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}")
            r = cr.get(u, impersonate="chrome", timeout=10, headers=JSON_HEADERS_FULL)
            if r.status_code == 200:
                d = r.json()
                prods = d.get("data", {}).get("items", []) or d.get("products", [])
                if prods:
                    p = prods[0]
                    prices = []
                    for s in p.get("sizes", [])[:3]:
                        pr = s.get("price", {})
                        for k in ("basic", "product", "card", "wallet", "old", "crossed"):
                            v = pr.get(k)
                            if v and v > 0:
                                prices.append(f"{k}={v}")
                    mark("WB", f"card_{ver}", True, f"items={len(prods)} prices={prices[:8]}")
                    return
                mark("WB", f"card_{ver}", False, f"200 no items keys={list(d.keys())[:5]}")
            else:
                mark("WB", f"card_{ver}", False, f"HTTP {r.status_code}")
        except Exception as e:
            mark("WB", f"card_{ver}", False, str(e)[:80])
        time.sleep(0.5)

    # S2: catalog.wb.ru (mirror)
    for ver in ["v1", "v2"]:
        try:
            u = (f"https://catalog.wb.ru/cards/{ver}/detail"
                 f"?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}")
            r = cr.get(u, impersonate="chrome", timeout=10, headers=JSON_HEADERS_FULL)
            if r.status_code == 200:
                d = r.json()
                prods = d.get("data", {}).get("items", []) or d.get("products", [])
                if prods:
                    p = prods[0]
                    prices = []
                    for s in p.get("sizes", [])[:3]:
                        pr = s.get("price", {})
                        for k in ("basic", "product", "card", "wallet"):
                            v = pr.get(k)
                            if v and v > 0:
                                prices.append(f"{k}={v}")
                    mark("WB", f"catalog_{ver}", True, f"items={len(prods)} prices={prices[:6]}")
                    return
                mark("WB", f"catalog_{ver}", False, f"200 no items")
            else:
                mark("WB", f"catalog_{ver}", False, f"HTTP {r.status_code}")
        except Exception as e:
            mark("WB", f"catalog_{ver}", False, str(e)[:80])
        time.sleep(0.5)

    # S3: web API (www.wildberries.ru/web/api)
    try:
        u = f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"
        r = cr.get(u, impersonate="chrome", timeout=12, headers=JSON_HEADERS_FULL)
        if r.status_code == 200:
            d = r.json()
            data = d.get("data", {})
            prices = data.get("prices") or data.get("price")
            if prices:
                mark("WB", "web_api_v1", True, f"prices={json.dumps(prices)[:100]}")
                return
            # Может быть в других ключах
            keys = list(d.keys())[:10]
            mark("WB", "web_api_v1", False, f"200 keys={keys} data_keys={list(data.keys())[:8]}")
        else:
            mark("WB", "web_api_v1", False, f"HTTP {r.status_code} body[:60]={r.text[:60]}")
    except Exception as e:
        mark("WB", "web_api_v1", False, str(e)[:80])

    # S4: HTML с полными заголовками (sec-ch-ua и т.д.)
    try:
        u = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
        r = cr.get(u, impersonate="chrome", timeout=15, headers=BROWSER_HEADERS)
        if r.status_code == 200:
            html = r.text
            prices_found = []
            # og:price
            m = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
            if not m:
                m = re.search(r'content=["\']([\d.]+)["\'][^>]*og:price:amount', html, re.I)
            if m: prices_found.append(f"og={m.group(1)}")
            # data-price
            for m2 in re.finditer(r'data-price=["\'](\d+)["\']', html, re.I):
                prices_found.append(f"data={m2.group(1)}")
            # JSON в HTML: "price":1234
            for m3 in re.finditer(r'"price"\s*:\s*(\d{4,})', html):
                prices_found.append(f"json={m3.group(1)}")
            # __NEXT_DATA__
            m4 = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
            if m4:
                try:
                    nd = json.loads(m4.group(1))
                    # Глубокий поиск price
                    def find_prices(obj, path=""):
                        found = []
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                if k in ("price", "basic", "product", "card") and isinstance(v, (int, float)) and v > 100:
                                    found.append(f"{path}.{k}={v}")
                                elif isinstance(v, (dict, list)):
                                    found.extend(find_prices(v, f"{path}.{k}"))
                        elif isinstance(obj, list):
                            for i, v in enumerate(obj[:5]):
                                found.extend(find_prices(v, f"{path}[{i}]"))
                        return found[:10]
                    jp = find_prices(nd)
                    if jp: prices_found.extend(jp[:5])
                except: pass
            mark("WB", "html_full", bool(prices_found),
                 f"len={len(html)} prices={prices_found[:8]}")
            if prices_found:
                return
        else:
            mark("WB", "html_full", False, f"HTTP {r.status_code} len={len(r.text)}")
    except Exception as e:
        mark("WB", "html_full", False, str(e)[:80])

    # S5: Мобильный API
    try:
        u = f"https://m.wildberries.ru/api/v1/catalog/cards?nm={nm_id}&curr=rub"
        r = cr.get(u, impersonate="chrome", timeout=10, headers=JSON_HEADERS_FULL)
        if r.status_code == 200:
            d = r.json()
            mark("WB", "mobile_api", True, f"keys={list(d.keys())[:8]}")
        else:
            mark("WB", "mobile_api", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("WB", "mobile_api", False, str(e)[:80])

    time.sleep(1)

# ============================================================
# OZON: two-step + entrypoint с cookie
# ============================================================
def ozon_price(listing_id="3481320800"):
    log.info("=== OZON (id=%s) ===", listing_id)
    from curl_cffi import requests as cr

    # S1: Two-step: GET page → cookies → entrypoint API
    try:
        # Step 1: GET product page (получить cookies)
        u1 = f"https://www.ozon.ru/product/{listing_id}/"
        r1 = cr.get(u1, impersonate="chrome", timeout=15, headers=BROWSER_HEADERS)
        cookies_str = r1.cookies.get_dict() if hasattr(r1, 'cookies') else {}
        log.info("OZON step1: HTTP %d cookies=%s", r1.status_code, list(cookies_str.keys())[:5])

        # Step 2: entrypoint API с cookies
        u2 = (f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
              f"?url=%2Fproduct%2F{listing_id}%2F%3F__rr%3D1%26layout_container%3DpdpPage2column")
        h2 = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="131", "Google Chrome";v="131"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Referer": f"https://www.ozon.ru/product/{listing_id}/",
        }
        r2 = cr.get(u2, impersonate="chrome", timeout=15, headers=h2)
        if r2.status_code == 200:
            d = r2.json()
            layout = d.get("layout", [])
            # Найти webPrice widget
            price_data = None
            title_data = None
            def walk(w):
                nonlocal price_data, title_data
                if isinstance(w, list):
                    for x in w: walk(x)
                elif isinstance(w, dict):
                    comp = w.get("component", "")
                    params_raw = w.get("params", "{}")
                    try:
                        params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
                    except: params = {}
                    if "Price" in comp or "price" in comp:
                        price_data = params
                    if "Title" in comp or "title" in comp.lower():
                        title_data = params
                    for ph in w.get("placeholders", []):
                        walk(ph.get("widgets", []))
            walk(layout)
            detail = f"layout={len(layout)}"
            if price_data: detail += f" price={json.dumps(price_data, ensure_ascii=False)[:100]}"
            if title_data: detail += f" title={json.dumps(title_data, ensure_ascii=False)[:60]}"
            mark("OZON", "two_step_entry", True, detail)
            # Сохраним
            with open("ozon_p6_entry.json", "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            return
        else:
            body = r2.text[:200]
            mark("OZON", "two_step_entry", False, f"HTTP {r2.status_code} body={body}")
    except Exception as e:
        mark("OZON", "two_step_entry", False, str(e)[:100])

    # S2: entrypoint без cookies, но с полным sec-ch-ua + abt_att
    try:
        u = (f"https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
             f"?url=%2Fproduct%2F{listing_id}%2F%3F__rr%3D1%26abt_att%3D1%26layout_container%3DpdpPage2column%26layout")
        h = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="131", "Google Chrome";v="131"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Referer": f"https://www.ozon.ru/product/{listing_id}/",
        }
        r = cr.get(u, impersonate="chrome", timeout=15, headers=h)
        if r.status_code == 200:
            d = r.json()
            mark("OZON", "entry_abt", True, f"layout={len(d.get('layout',[]))} keys={list(d.keys())[:6]}")
        else:
            mark("OZON", "entry_abt", False, f"HTTP {r.status_code} body[:80]={r.text[:80]}")
    except Exception as e:
        mark("OZON", "entry_abt", False, str(e)[:80])

    # S3: HTML с двумя запросами (первый — warm-up)
    try:
        # Warm-up
        cr.get(f"https://www.ozon.ru/", impersonate="chrome", timeout=8,
               headers={"Accept": "text/html"})
        time.sleep(1)
        # Real
        u = f"https://www.ozon.ru/product/{listing_id}/"
        r = cr.get(u, impersonate="chrome", timeout=15, headers=BROWSER_HEADERS)
        if r.status_code == 200:
            html = r.text
            # JSON-LD
            ld_blocks = re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL, re.I)
            product_ld = None
            for block in ld_blocks:
                try:
                    d = json.loads(block.strip())
                    if isinstance(d, dict) and d.get("@type") == "Product":
                        product_ld = d
                        break
                    if isinstance(d, list):
                        for item in d:
                            if isinstance(item, dict) and item.get("@type") == "Product":
                                product_ld = item
                                break
                except: pass
            # og: meta
            og_title = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
            og_price = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
            detail = f"len={len(html)} ld={len(ld_blocks)}"
            if product_ld:
                offers = product_ld.get("offers", {})
                detail += f" ld_name={str(product_ld.get('name',''))[:40]} ld_price={offers.get('price','?')}"
            if og_title: detail += f" og_t={og_title.group(1)[:30]}"
            if og_price: detail += f" og_p={og_price.group(1)}"
            mark("OZON", "html_warmup", bool(product_ld or og_price), detail)
            if product_ld or og_price:
                return
        else:
            mark("OZON", "html_warmup", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("OZON", "html_warmup", False, str(e)[:80])

    time.sleep(1)

# ============================================================
# AVITO: найти реальный listing + fetch
# ============================================================
def avito_fetch():
    log.info("=== AVITO ===")
    from curl_cffi import requests as cr

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="131", "Google Chrome";v="131"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    listing_id = None

    # S1: Поиск через search.avito.ru (JSON API)
    try:
        u = "https://search.avito.ru/api/search?query=iphone&city=1&sort=price_desc&limit=5"
        r = cr.get(u, impersonate="chrome", timeout=12, headers={
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://www.avito.ru/moskva",
        })
        if r.status_code == 200:
            d = r.json()
            offers = d.get("offers", [])
            if offers:
                listing_id = str(offers[0].get("id", ""))
                mark("AVITO", "search_api", True, f"id={listing_id} title={str(offers[0].get('title',''))[:40]}")
            else:
                mark("AVITO", "search_api", False, f"200 no offers keys={list(d.keys())[:5]}")
        else:
            mark("AVITO", "search_api", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("AVITO", "search_api", False, str(e)[:80])

    # S2: Если не нашли — категория (с паузой)
    if not listing_id:
        time.sleep(2)
        try:
            u = "https://www.avito.ru/moskva/telefony-i-smartfony"
            r = cr.get(u, impersonate="chrome", timeout=15, headers=headers)
            if r.status_code == 200:
                html = r.text
                # Паттерны для ID
                ids = re.findall(r'avito\.ru/.*/(\d{9,12})/?"', html)
                if not ids:
                    ids = re.findall(r'href="[^"]*?/(\d{9,12})"', html)
                if not ids:
                    ids = re.findall(r'"id":(\d{9,12})', html)
                if ids:
                    listing_id = ids[0]
                    mark("AVITO", "category", True, f"id={listing_id} candidates={len(ids)}")
                else:
                    mark("AVITO", "category", False, f"len={len(html)} no IDs")
            else:
                mark("AVITO", "category", False, f"HTTP {r.status_code}")
        except Exception as e:
            mark("AVITO", "category", False, str(e)[:80])

    if not listing_id:
        mark("AVITO", "find", False, "no listing found, skipping fetch")
        return

    log.info("AVITO listing: %s", listing_id)

    # S3: Fetch listing (www → m)
    for prefix in ("www", "m"):
        try:
            u = f"https://{prefix}.avito.ru/ad/{listing_id}"
            r = cr.get(u, impersonate="chrome", timeout=15, headers=headers)
            if r.status_code == 200 and len(r.text) > 2000:
                html = r.text
                has_captcha = any(x in html.lower() for x in ("hcaptcha", "smartcaptcha", "cf-challenge"))
                # JSON-LD
                ld_blocks = re.findall(
                    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                    html, re.DOTALL, re.I)
                product_ld = None
                for block in ld_blocks:
                    try:
                        d = json.loads(block.strip())
                        if isinstance(d, dict) and d.get("@type") == "Product":
                            product_ld = d
                            break
                    except: pass
                # og:
                og_title = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
                og_price = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
                data_price = re.search(r'data-price=["\'](\d+)["\']', html, re.I)
                # __PRELOADED_STATE__
                state_m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)

                detail = f"len={len(html)} cap={has_captcha} ld={len(ld_blocks)}"
                if og_title: detail += f" t={og_title.group(1)[:35]}"
                if og_price: detail += f" og_p={og_price.group(1)}"
                if data_price: detail += f" dp={data_price.group(1)}"
                if product_ld:
                    offers = product_ld.get("offers", {})
                    detail += f" ld_p={offers.get('price','?')}"
                if state_m: detail += " state=YES"
                mark("AVITO", f"fetch_{prefix}", True, detail)
                with open(f"avito_p6_{prefix}.html", "w", encoding="utf-8") as f:
                    f.write(html[:80000])
                break
            else:
                mark("AVITO", f"fetch_{prefix}", False,
                     f"HTTP {r.status_code} len={len(r.text) if r.text else 0}")
        except Exception as e:
            mark("AVITO", f"fetch_{prefix}", False, str(e)[:80])
        time.sleep(1.5)

# ============================================================
if __name__ == "__main__":
    t0 = time.time()
    wb_price()
    ozon_price()
    avito_fetch()
    dt = time.time() - t0

    log.info("=== PROBE6 DONE in %.1fs ===", dt)
    with open("probe6_results.json", "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    for k, v in RESULTS.items():
        log.info("  %s: %s %s", k, "PASS" if v["ok"] else "FAIL", v["detail"][:120])
