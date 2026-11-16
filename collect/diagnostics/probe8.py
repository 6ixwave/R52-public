r"""probe8.py — Multi-strategy: WB prices(7), Ozon(4), Avito(3), Farpost retest.
Run: C:\Python315\python.exe probe8.py
"""
import sys, os, json, time, re, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

import urllib3
urllib3.disable_warnings()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("P8")

RESULTS = {}
def mark(src, label, ok, detail=""):
    RESULTS[f"{src}:{label}"] = {"ok": ok, "detail": detail}
    log.info("[%s] %s → %s %s", src, label, "PASS" if ok else "FAIL", detail[:150])

def _deep_get(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d

# ============================================================
# WB: 7 price strategies
# ============================================================
def wb_prices():
    log.info("=== WB PRICES (nm_id=1097975275) ===")
    from curl_cffi import requests as cr
    nm_id = 1097975275
    vol, part = nm_id // 100000, nm_id // 1000
    host_num = 42

    full_h = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.wildberries.ru/catalog/1097975275/detail.aspx",
        "Origin": "https://www.wildberries.ru",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="131", "Google Chrome";v="131"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }

    # S1: card.wb.ru with full browser headers
    for ver in ("v1","v2","v4","v5"):
        try:
            u = f"https://card.wb.ru/cards/{ver}/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"
            r = cr.get(u, impersonate="chrome", timeout=10, headers=full_h)
            if r.status_code == 200:
                d = r.json()
                prods = d.get("data",{}).get("items",[]) or d.get("products",[])
                if prods:
                    p = prods[0]
                    prices = []
                    for s in p.get("sizes",[])[:2]:
                        pr = s.get("price",{})
                        for k in ("basic","product","card","wallet","old"):
                            v = pr.get(k)
                            if v and v > 0: prices.append(f"{k}={v}")
                    mark("WB", f"card_{ver}_full", True, f"items={len(prods)} prices={prices[:6]}")
                    return
                mark("WB", f"card_{ver}_full", False, f"200 no items keys={list(d.keys())[:5]}")
            else:
                mark("WB", f"card_{ver}_full", False, f"HTTP {r.status_code}")
        except Exception as e:
            mark("WB", f"card_{ver}_full", False, str(e)[:60])
        time.sleep(0.5)

    # S2: catalog.wb.ru
    for ver in ("v1","v2"):
        try:
            u = f"https://catalog.wb.ru/cards/{ver}/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"
            r = cr.get(u, impersonate="chrome", timeout=10, headers=full_h)
            if r.status_code == 200:
                d = r.json()
                prods = d.get("data",{}).get("items",[]) or d.get("products",[])
                if prods:
                    p = prods[0]
                    prices = []
                    for s in p.get("sizes",[])[:2]:
                        pr = s.get("price",{})
                        for k in ("basic","product","card","wallet","old"):
                            v = pr.get(k)
                            if v and v > 0: prices.append(f"{k}={v}")
                    mark("WB", f"catalog_{ver}", True, f"items={len(prods)} prices={prices[:6]}")
                    return
                mark("WB", f"catalog_{ver}", False, f"200 no items")
            else:
                mark("WB", f"catalog_{ver}", False, f"HTTP {r.status_code}")
        except Exception as e:
            mark("WB", f"catalog_{ver}", False, str(e)[:60])
        time.sleep(0.5)

    # S3: Mobile API (app.wildberries.ru)
    try:
        u = f"https://app.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"
        r = cr.get(u, impersonate="chrome", timeout=10, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
            "Referer": "https://www.wildberries.ru/",
        })
        if r.status_code == 200:
            d = r.json()
            data = d.get("data", d)
            prices = data.get("prices") or data.get("price")
            mark("WB", "mobile_api", True, f"keys={list(d.keys())[:8]} prices={json.dumps(prices)[:80] if prices else 'none'}")
        else:
            mark("WB", "mobile_api", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("WB", "mobile_api", False, str(e)[:60])

    # S4: www.wildberries.ru web API
    for ver in ("v1","v2","v5"):
        try:
            u = f"https://www.wildberries.ru/web/api/catalog/cards/{ver}/detail?nm={nm_id}"
            r = cr.get(u, impersonate="chrome", timeout=10, headers=full_h)
            if r.status_code == 200:
                d = r.json()
                mark("WB", f"web_api_{ver}", True, f"keys={list(d.keys())[:8]}")
                return
            else:
                mark("WB", f"web_api_{ver}", False, f"HTTP {r.status_code}")
        except Exception as e:
            mark("WB", f"web_api_{ver}", False, str(e)[:60])
        time.sleep(0.5)

    # S5: basket CDN price paths
    base = f"https://basket-{host_num:02d}.wbbasket.ru/vol{vol}/part{part}/{nm_id}"
    for sub in ("info/ru/prices.json","prices.json","info/prices.json","price.json","info/ru/price.json","data.json"):
        try:
            r = cr.get(f"{base}/{sub}", impersonate="chrome", timeout=8, headers={"Accept": "application/json"})
            if r.status_code == 200:
                d = r.json()
                mark("WB", f"cdn_{sub.replace('/','_')}", True, f"data={json.dumps(d)[:120]}")
                return
            elif r.status_code != 404:
                mark("WB", f"cdn_{sub.replace('/','_')}", False, f"HTTP {r.status_code}")
        except Exception as e:
            mark("WB", f"cdn_{sub.replace('/','_')}", False, str(e)[:50])
        time.sleep(0.3)

    # S6: search.wb.ru variants
    for params in (
        f"?appType=1&curr=rub&dest=-1257786&nm={nm_id}",
        f"?curr=rub&nm={nm_id}",
        f"?appType=1&curr=rub&dest=-1&nm={nm_id}",
    ):
        try:
            u = f"https://search.wb.ru/cards/detail{params}"
            r = cr.get(u, impersonate="chrome", timeout=10, headers=full_h)
            if r.status_code == 200:
                d = r.json()
                prods = d.get("data",{}).get("items",[]) or d.get("products",[])
                if prods:
                    mark("WB", f"search_{params[:25]}", True, f"items={len(prods)}")
                    return
                mark("WB", f"search_{params[:25]}", False, f"200 no items")
            else:
                mark("WB", f"search_{params[:25]}", False, f"HTTP {r.status_code}")
        except Exception as e:
            mark("WB", f"search_{params[:25]}", False, str(e)[:50])
        time.sleep(0.5)

    # S7: card.wb.ru with cookie from main page
    try:
        r0 = cr.get("https://www.wildberries.ru/", impersonate="chrome", timeout=10)
        cookies = r0.cookies.get_dict()
        log.info("WB main cookies: %s", list(cookies.keys())[:5])
        time.sleep(1)
        u = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"
        r = cr.get(u, impersonate="chrome", timeout=10, headers=full_h, cookies=cookies)
        if r.status_code == 200:
            d = r.json()
            prods = d.get("data",{}).get("items",[]) or d.get("products",[])
            if prods:
                mark("WB", "card_cookie", True, f"items={len(prods)}")
            else:
                mark("WB", "card_cookie", False, f"200 no items")
        else:
            mark("WB", "card_cookie", False, f"HTTP {r.status_code}")
    except Exception as e:
        mark("WB", "card_cookie", False, str(e)[:60])

    time.sleep(1)

# ============================================================
# OZON: 4 strategies
# ============================================================
def ozon_strategies():
    log.info("=== OZON STRATEGIES ===")
    import requests as req
    from curl_cffi import requests as cr

    ozon_ids = ["2067947177", "3481320800", "1753035248", "2679464956"]

    # S1: Composer API with requests (bypass curl_cffi SSL)
    for oid in ozon_ids[:3]:
        try:
            u = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oid}/"
            h = {
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Referer": "https://www.ozon.ru/",
            }
            r = req.get(u, headers=h, timeout=12, verify=False)
            if r.status_code == 200:
                d = r.json()
                title = _deep_get(d,"page","title") or _deep_get(d,"page","seoTitle") or _deep_get(d,"product","title") or _deep_get(d,"product","name")
                mark("OZON", f"composer_req_{oid}", bool(title), f"title={str(title)[:40]} keys={list(d.keys())[:6]}")
                if title:
                    with open(f"ozon_p8_composer_{oid}.json","w",encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False, indent=2)
                    return
            else:
                mark("OZON", f"composer_req_{oid}", False, f"HTTP {r.status_code} body={r.text[:80]}")
        except Exception as e:
            mark("OZON", f"composer_req_{oid}", False, str(e)[:60])
        time.sleep(1)

    # S2: Composer API with curl_cffi verify=False
    for oid in ozon_ids[2:]:
        try:
            u = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{oid}/"
            r = cr.get(u, impersonate="chrome", timeout=12, verify=False, headers={
                "Accept": "application/json",
                "Referer": "https://www.ozon.ru/",
            })
            if r.status_code == 200:
                d = r.json()
                title = _deep_get(d,"page","title") or _deep_get(d,"product","title") or _deep_get(d,"product","name")
                mark("OZON", f"composer_cffi_{oid}", bool(title), f"title={str(title)[:40]}")
                if title:
                    return
            else:
                mark("OZON", f"composer_cffi_{oid}", False, f"HTTP {r.status_code}")
        except Exception as e:
            mark("OZON", f"composer_cffi_{oid}", False, str(e)[:60])
        time.sleep(1)

    # S3: Direct HTML with requests (full browser headers)
    for oid in ozon_ids[:2]:
        try:
            u = f"https://www.ozon.ru/product/{oid}/"
            h = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="131", "Google Chrome";v="131"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
            r = req.get(u, headers=h, timeout=15, verify=False)
            if r.status_code == 200 and len(r.text) > 2000:
                og_t = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', r.text, re.I)
                og_p = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', r.text, re.I)
                detail = f"len={len(r.text)}"
                if og_t: detail += f" t={og_t.group(1)[:40]}"
                if og_p: detail += f" p={og_p.group(1)}"
                mark("OZON", f"html_req_{oid}", bool(og_t or og_p), detail)
                if og_t:
                    with open(f"ozon_p8_{oid}.html","w",encoding="utf-8") as f:
                        f.write(r.text[:100000])
                    return
            else:
                mark("OZON", f"html_req_{oid}", False, f"HTTP {r.status_code} len={len(r.text)}")
        except Exception as e:
            mark("OZON", f"html_req_{oid}", False, str(e)[:60])
        time.sleep(2)

    # S4: Mobile UA
    try:
        oid = ozon_ids[0]
        u = f"https://www.ozon.ru/product/{oid}/"
        r = req.get(u, headers={
            "Accept": "text/html",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }, timeout=15, verify=False)
        if r.status_code == 200 and len(r.text) > 2000:
            og_t = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', r.text, re.I)
            mark("OZON", f"mobile_{oid}", bool(og_t), f"len={len(r.text)} t={og_t.group(1)[:30] if og_t else '?'}")
        else:
            mark("OZON", f"mobile_{oid}", False, f"HTTP {r.status_code} len={len(r.text)}")
    except Exception as e:
        mark("OZON", f"mobile_{oid}", False, str(e)[:60])

    time.sleep(1)

# ============================================================
# AVITO: 3 strategies
# ============================================================
def avito_strategies():
    log.info("=== AVITO STRATEGIES ===")
    import requests as req
    from curl_cffi import requests as cr

    # S1: Warmup + category with 5s delay
    try:
        r0 = cr.get("https://www.avito.ru/", impersonate="chrome", timeout=12)
        log.info("AVITO warmup: HTTP %d len=%d", r0.status_code, len(r0.text))
        time.sleep(5)
    except Exception as e:
        log.info("AVITO warmup: %s", str(e)[:60])

    cat_urls = [
        "https://www.avito.ru/moskva/telefony-i-smartfony",
        "https://www.avito.ru/spb/telefony-i-smartfony",
        "https://www.avito.ru/moskva/noutbuki",
    ]
    found_id = None
    for cu in cat_urls:
        try:
            r = cr.get(cu, impersonate="chrome", timeout=15)
            if r.status_code == 200 and len(r.text) > 5000:
                ids = re.findall(r'href="[^"]*?/(\d{9,12})/?"', r.text)
                if not ids:
                    ids = re.findall(r'"id"\s*:\s*"?(\d{9,12})"?', r.text)
                if not ids:
                    ids = re.findall(r'data-ad-id=["\'](\d+)', r.text)
                if ids:
                    found_id = ids[0]
                    mark("AVITO", f"cat_{cu.split('/')[-1]}", True, f"id={ids[0]} n={len(ids)}")
                    break
                else:
                    mark("AVITO", f"cat_{cu.split('/')[-1]}", False, f"len={len(r.text)} no IDs")
            else:
                mark("AVITO", f"cat_{cu.split('/')[-1]}", False, f"HTTP {r.status_code} len={len(r.text)}")
        except Exception as e:
            mark("AVITO", f"cat_{cu.split('/')[-1]}", False, str(e)[:60])
        time.sleep(4)

    # S2: m.avito.ru
    try:
        u = "https://m.avito.ru/moskva/telefony-i-smartfony"
        r = req.get(u, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            "Accept": "text/html",
        }, timeout=15, verify=False)
        if r.status_code == 200 and len(r.text) > 3000:
            ids = re.findall(r'href="[^"]*?/(\d{9,12})/?"', r.text)
            if not ids:
                ids = re.findall(r'"id"\s*:\s*"?(\d{9,12})"?', r.text)
            mark("AVITO", "m_cat", bool(ids), f"len={len(r.text)} ids={len(ids)}")
            if ids and not found_id:
                found_id = ids[0]
        else:
            mark("AVITO", "m_cat", False, f"HTTP {r.status_code} len={len(r.text)}")
    except Exception as e:
        mark("AVITO", "m_cat", False, str(e)[:60])

    # S3: Fetch found listing
    if found_id:
        try:
            u = f"https://www.avito.ru/ad/{found_id}"
            r = cr.get(u, impersonate="chrome", timeout=15)
            if r.status_code == 200 and len(r.text) > 3000:
                html = r.text
                has_captcha = any(x in html.lower() for x in ("hcaptcha","smartcaptcha","cf-challenge"))
                og_t = re.search(r'og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
                og_p = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
                detail = f"len={len(html)} cap={has_captcha}"
                if og_t: detail += f" t={og_t.group(1)[:30]}"
                if og_p: detail += f" p={og_p.group(1)}"
                mark("AVITO", f"fetch_{found_id}", True, detail)
                with open(f"avito_p8_{found_id}.html","w",encoding="utf-8") as f:
                    f.write(html[:100000])
            else:
                mark("AVITO", f"fetch_{found_id}", False, f"HTTP {r.status_code} len={len(r.text)}")
        except Exception as e:
            mark("AVITO", f"fetch_{found_id}", False, str(e)[:60])
    else:
        mark("AVITO", "fetch", False, "no listing found")

    time.sleep(1)

# ============================================================
# FARPOST: quick retest
# ============================================================
def farpost_retest():
    log.info("=== FARPOST RETEST ===")
    from r52.collectors.farpost import FarpostCollector
    col = FarpostCollector(data_dir=None, use_proxies=False)
    for lid in ("138859452", "138952176"):
        try:
            url = col.build_url(lid)
            t0 = time.time()
            result = col.collect_product(lid, url, f"FP-{lid}")
            dt = time.time() - t0
            if result:
                obs = result.get("observation", {})
                prices = obs.get("price_candidates", [])
                title = obs.get("fields",{}).get("title",{}).get("value","")
                media = obs.get("media",{}).get("items",[])
                mark("FARPOST", f"{lid}", True, f"{dt:.1f}s t={title[:30]} p={len(prices)} m={len(media)}")
            else:
                mark("FARPOST", f"{lid}", False, f"{dt:.1f}s None")
        except Exception as e:
            mark("FARPOST", f"{lid}", False, f"{type(e).__name__}: {str(e)[:80]}")
        time.sleep(2)
    col.session.close()

# ============================================================
if __name__ == "__main__":
    t0 = time.time()
    wb_prices()
    ozon_strategies()
    avito_strategies()
    farpost_retest()
    dt = time.time() - t0
    log.info("=== PROBE8 DONE in %.1fs ===", dt)
    with open("probe8_results.json","w",encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    for k,v in RESULTS.items():
        log.info("  %s: %s %s", k, "PASS" if v["ok"] else "FAIL", v["detail"][:120])
