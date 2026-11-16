#!/usr/bin/env python3
"""diag_multi11.py — Round 2: WB price, Ozon composer, FP cookie-flow, AV alt."""
import json, time, re, logging, os
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger("diag11")

results = {}
t0 = time.monotonic()

# ============================================================
# WB PRICE API (card.wb.ru, catalog.wb.ru, web API)
# ============================================================
WB_NM = 1097975275

def test_wb_price():
    t = time.monotonic()
    try:
        from curl_cffi import requests as cffi_req
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://www.wildberries.ru/",
            "Origin": "https://www.wildberries.ru",
        }
        endpoints = [
            ("card_v1", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}"),
            ("card_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}"),
            ("card_v4", f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}"),
            ("card_v5", f"https://card.wb.ru/cards/v5/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}"),
            ("catalog_v1", f"https://catalog.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}"),
            ("web_api", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={WB_NM}"),
            ("search_wb", f"https://search.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}"),
        ]
        for label, url in endpoints:
            try:
                resp = cffi_req.get(url, impersonate="chrome", timeout=12, headers=headers)
                st = resp.status_code
                if st == 200:
                    data = resp.json()
                    products = data.get("products") or data.get("data", {}).get("items", [])
                    if products:
                        p = products[0]
                        prices = {}
                        for sz in p.get("sizes", []):
                            pr = sz.get("price", {})
                            if pr:
                                prices = pr
                                break
                        qty = None
                        for sz in p.get("sizes", []):
                            if sz.get("qty") is not None:
                                qty = sz["qty"]
                                break
                        detail = f"HTTP=200 name={str(p.get('name','?'))[:40]} prices={prices} qty={qty}"
                        log.info("WB:%s %s", label, detail)
                        results[f"WB:{label}"] = {"ok": True, "detail": detail, "s": round(time.monotonic()-t,1)}
                        with open(f"d11_wb_{label}.json","w",encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        return True
                    elif "data" in data and isinstance(data["data"], dict):
                        d = data["data"]
                        prices = d.get("prices") or d.get("price")
                        qty = d.get("qty")
                        detail = f"HTTP=200 data.keys={list(d.keys())[:10]} prices={prices} qty={qty}"
                        log.info("WB:%s %s", label, detail)
                        results[f"WB:{label}"] = {"ok": bool(prices), "detail": detail, "s": round(time.monotonic()-t,1)}
                        if prices:
                            return True
                else:
                    log.info("WB:%s HTTP=%d", label, st)
            except Exception as e:
                log.info("WB:%s err=%s", label, str(e)[:60])
            time.sleep(0.5)
        results.setdefault("WB:price", {"ok": False, "detail": "all endpoints failed", "s": round(time.monotonic()-t,1)})
        return False
    except Exception as e:
        results["WB:price"] = {"ok": False, "detail": f"error={e}", "s": round(time.monotonic()-t,1)}
        return False

# ============================================================
# OZON COMPOSER API (fast JSON, no browser)
# ============================================================
OZON_ID = "3481320800"

def test_ozon_composer():
    t = time.monotonic()
    try:
        from curl_cffi import requests as cffi_req
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://www.ozon.ru/",
        }
        endpoints = [
            ("composer_v2", f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{OZON_ID}/"),
            ("pdp_entry", f"https://www.ozon.ru/api/entrypoint/pdp/{OZON_ID}"),
            ("api_card", f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=%2Fproduct%2F{OZON_ID}%2F"),
        ]
        for label, url in endpoints:
            try:
                resp = cffi_req.get(url, impersonate="chrome", timeout=15, headers=headers)
                st = resp.status_code
                log.info("OZON:%s HTTP=%d len=%d", label, st, len(resp.content))
                if st == 200:
                    data = resp.json()
                    # Look for title and price in various paths
                    title = None
                    price = None
                    # Deep search
                    def find_key(obj, keys, depth=0):
                        if depth > 6 or not isinstance(obj, (dict, list)):
                            return None
                        if isinstance(obj, list):
                            for item in obj[:20]:
                                r = find_key(item, keys, depth+1)
                                if r: return r
                            return None
                        for k in keys:
                            if k in obj:
                                return obj[k]
                        for v in obj.values():
                            if isinstance(v, (dict, list)):
                                r = find_key(v, keys, depth+1)
                                if r: return r
                        return None
                    title = find_key(data, ["title","name"])
                    price_obj = find_key(data, ["price","priceInfo","priceV2"])
                    if isinstance(price_obj, dict):
                        for pk in ("price","current","basic","old","crossed","value","min"):
                            pv = price_obj.get(pk)
                            if isinstance(pv,(int,float)) and pv > 0:
                                price = pv
                                break
                    elif isinstance(price_obj,(int,float)):
                        price = price_obj
                    detail = f"HTTP=200 title={str(title)[:50]} price={price} keys={list(data.keys())[:8]}"
                    log.info("OZON:%s %s", label, detail)
                    results[f"OZON:{label}"] = {"ok": bool(title and price), "detail": detail, "s": round(time.monotonic()-t,1)}
                    if title and price:
                        with open(f"d11_ozon_{label}.json","w",encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        return True
                else:
                    results[f"OZON:{label}"] = {"ok": False, "detail": f"HTTP={st}", "s": round(time.monotonic()-t,1)}
            except Exception as e:
                log.info("OZON:%s err=%s", label, str(e)[:80])
                results[f"OZON:{label}"] = {"ok": False, "detail": f"err={str(e)[:80]}", "s": round(time.monotonic()-t,1)}
            time.sleep(1)
        return False
    except Exception as e:
        results["OZON:composer"] = {"ok": False, "detail": f"error={e}", "s": round(time.monotonic()-t,1)}
        return False

# ============================================================
# FARPOST: better cookie flow
# ============================================================
FP_ID = "138859452"

def test_farpost_v2():
    t = time.monotonic()
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        })
        # Step 1: warmup
        r0 = s.get("https://www.farpost.ru/", timeout=(10, 20))
        c1 = dict(s.cookies)
        log.info("FP:v2 warmup HTTP=%d cookies=%s", r0.status_code, list(c1.keys()))
        time.sleep(3)

        # Step 2: try /bulletin/ path (different from /vladivostok/)
        urls = [
            f"https://www.farpost.ru/vladivostok/{FP_ID}.html",
            f"https://www.farpost.ru/bulletin/{FP_ID}",
        ]
        for i, url in enumerate(urls):
            r1 = s.get(url, timeout=(10, 25), allow_redirects=True)
            final_url = r1.url
            ct = (r1.headers.get("Content-Type") or "").lower()
            if "1251" in ct or "cp1251" in ct:
                html = r1.content.decode("windows-1251", errors="replace")
            else:
                html = r1.text
            is_verify = "/verify" in final_url
            prices = []
            m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
            if m:
                prices.append(int(m.group(1)))
            for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL|re.I):
                try:
                    d = json.loads(m.group(1).strip())
                    items = d if isinstance(d, list) else [d]
                    for it in items:
                        if isinstance(it, dict) and it.get("@type")=="Product":
                            off = it.get("offers",{})
                            if isinstance(off, dict): off=[off]
                            for o in off:
                                if isinstance(o,dict) and o.get("price"):
                                    prices.append(float(o["price"]))
                except: pass
            c2 = dict(s.cookies)
            detail = f"HTTP={r1.status_code} len={len(html)} verify={is_verify} url={final_url[:60]} prices={prices[:4]} cookies={list(c2.keys())}"
            log.info("FP:v2[%d] %s", i, detail)
            results[f"FP:v2_{i}"] = {"ok": len(prices)>0 and not is_verify, "detail": detail, "s": round(time.monotonic()-t,1)}
            if prices and not is_verify:
                return True
            time.sleep(3)
        return False
    except Exception as e:
        results["FP:v2"] = {"ok": False, "detail": f"error={e}", "s": round(time.monotonic()-t,1)}
        return False

# ============================================================
# AVITO: alternate approach
# ============================================================
def test_avito_v2():
    t = time.monotonic()
    try:
        from curl_cffi import requests as cffi_req
        # Try multiple listing formats
        urls = [
            "https://www.avito.ru/vladivostok/telefony-ADv8qA09qCq",
            "https://www.avito.ru/vladivostok/telefony-ADv8qA09qCq?from=search_suggest",
        ]
        for i, url in enumerate(urls):
            try:
                resp = cffi_req.get(url, impersonate="chrome", timeout=15, headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                    "Referer": "https://www.avito.ru/",
                })
                html = resp.text
                captcha = ("captcha" in html.lower() or "hcaptcha" in html.lower()
                           or "challenge" in html.lower() or "cf-chl" in html.lower())
                prices = []
                for m in re.finditer(r'(\d[\d\s\u00a0]{2,9})\s*(?:\u20bd|руб)', html, re.I):
                    try:
                        v = float(m.group(1).replace(" ","").replace("\xa0",""))
                        if 100 < v < 10000000:
                            prices.append(v)
                    except: pass
                # JSON-LD
                for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL|re.I):
                    try:
                        d = json.loads(m.group(1).strip())
                        items = d if isinstance(d, list) else [d]
                        for it in items:
                            if isinstance(it, dict) and it.get("@type")=="Product":
                                off = it.get("offers",{})
                                if isinstance(off,dict): off=[off]
                                for o in off:
                                    if isinstance(o,dict) and o.get("price"):
                                        prices.append(float(o["price"]))
                    except: pass
                detail = f"HTTP={resp.status_code} len={len(html)} captcha={captcha} prices={prices[:5]}"
                log.info("AV:v2[%d] %s", i, detail)
                results[f"AV:v2_{i}"] = {"ok": len(prices)>0 and not captcha, "detail": detail, "s": round(time.monotonic()-t,1)}
                if prices and not captcha:
                    return True
            except Exception as e:
                log.info("AV:v2[%d] err=%s", i, str(e)[:60])
            time.sleep(2)
        return False
    except Exception as e:
        results["AV:v2"] = {"ok": False, "detail": f"error={e}", "s": round(time.monotonic()-t,1)}
        return False

# ============================================================
# RUN
# ============================================================
log.info("=== diag_multi11 START ===")
test_wb_price()
test_ozon_composer()
test_farpost_v2()
test_avito_v2()

total = round(time.monotonic() - t0, 1)
results["total_s"] = total
results["time"] = datetime.now().isoformat()

with open("d11_results.json","w",encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

log.info("=== diag_multi11 DONE in %ss ===", total)
for k,v in results.items():
    if k not in ("total_s","time"):
        log.info("  %-16s ok=%-5s %s", k, v.get("ok"), str(v.get("detail",""))[:130])
