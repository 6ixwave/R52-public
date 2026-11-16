#!/usr/bin/env python3
"""diag_multi10.py — Fast multi-platform diagnostic (Ozon, WB, Farpost, Avito)."""
import json, time, re, logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger("diag10")

results = {}
t0 = time.monotonic()

# === OZON ===
OZON_URL = "https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-3481320800/"
OZON_ID = "3481320800"

def test_ozon():
    t = time.monotonic()
    try:
        from curl_cffi import requests as cffi_req
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }
        resp = cffi_req.get(OZON_URL, impersonate="chrome", timeout=20, headers=headers)
        status = resp.status_code
        html = resp.text
        log.info("OZON:cffi HTTP=%d len=%d", status, len(html))

        prices = []
        jsonld_found = False
        title = None

        # Extract JSON-LD
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.I):
            raw = m.group(1).strip()
            try:
                data = json.loads(raw)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        jsonld_found = True
                        title = item.get("name")
                        offers = item.get("offers", {})
                        if isinstance(offers, dict):
                            offers = [offers]
                        for o in offers:
                            if isinstance(o, dict):
                                p = o.get("price")
                                if p:
                                    prices.append(float(p))
                        break
            except Exception:
                pass

        # og:price
        m = re.search(r'<meta[^>]*property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if m:
            prices.append(float(m.group(1)))

        # data-price
        for m in re.finditer(r'data-price=["\'](\d+)["\']', html):
            prices.append(int(m.group(1)))

        # Visible price text
        seen = set()
        for m in re.finditer(r'(\d[\d\s\u00a0]{1,9})\s*(?:\u20bd|руб)', html, re.I):
            try:
                v = float(m.group(1).replace(" ", "").replace("\xa0", ""))
                if 1 < v < 10000000 and v not in seen:
                    seen.add(v)
                    prices.append(v)
            except Exception:
                pass

        ok = jsonld_found and len(prices) > 0
        results["OZON:cffi"] = {
            "ok": ok,
            "detail": f"HTTP={status} len={len(html)} jsonld={jsonld_found} title={str(title)[:60]} prices={prices[:6]}",
            "s": round(time.monotonic() - t, 1),
        }
        # Save HTML snippet for debugging
        with open("d10_ozon_head.html", "w", encoding="utf-8") as f:
            f.write(html[:5000])
        return ok
    except Exception as e:
        results["OZON:cffi"] = {"ok": False, "detail": f"error={e}", "s": round(time.monotonic() - t, 1)}
        return False

# === WB ===
WB_NM = 1097975275

def test_wb():
    t = time.monotonic()
    try:
        from curl_cffi import requests as cffi_req
        vol = WB_NM // 100000
        part = WB_NM // 1000
        path = f"/vol{vol}/part{part}/{WB_NM}/info/ru/card.json"
        host = "basket-42.wbbasket.ru"
        url = f"https://{host}{path}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://www.wildberries.ru/",
            "Origin": "https://www.wildberries.ru",
        }
        resp = cffi_req.get(url, impersonate="chrome", timeout=15, headers=headers)
        status = resp.status_code
        log.info("WB:basket HTTP=%d len=%d", status, len(resp.content))

        if status != 200:
            results["WB:basket"] = {"ok": False, "detail": f"HTTP={status}", "s": round(time.monotonic() - t, 1)}
            return False

        data = resp.json()
        keys = list(data.keys())
        log.info("WB:basket keys=%s", keys)

        # Look for price in various places
        price_found = []
        selling = data.get("selling", {})
        if isinstance(selling, dict):
            for k, v in selling.items():
                if isinstance(v, (int, float)) and v > 0:
                    price_found.append(f"selling.{k}={v}")

        for k in ("price", "priceU", "priceD", "oldPrice"):
            v = data.get(k)
            if isinstance(v, (int, float)) and v > 0:
                price_found.append(f"top.{k}={v}")

        sizes = data.get("sizes", [])
        if isinstance(sizes, list):
            for sz in sizes[:5]:
                if isinstance(sz, dict):
                    sp = sz.get("price", {})
                    if isinstance(sp, dict):
                        for k, v in sp.items():
                            if isinstance(v, (int, float)) and v > 0:
                                price_found.append(f"sizes.price.{k}={v}")
                    qty = sz.get("qty")
                    if qty is not None:
                        price_found.append(f"sizes.qty={qty}")

        title = data.get("imt_name", "")
        ok = bool(title) and len(price_found) > 0
        results["WB:basket"] = {
            "ok": ok,
            "detail": f"HTTP={status} title={str(title)[:50]} nkeys={len(keys)} prices={price_found[:10]}",
            "s": round(time.monotonic() - t, 1),
        }

        with open("d10_wb_card.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return ok
    except Exception as e:
        results["WB:basket"] = {"ok": False, "detail": f"error={e}", "s": round(time.monotonic() - t, 1)}
        return False

# === FARPOST ===
FP_ID = "138859452"

def test_farpost():
    t = time.monotonic()
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        })

        # Warm-up
        r0 = s.get("https://www.farpost.ru/", timeout=(10, 20))
        cookies = list(s.cookies.keys())
        log.info("FP:warmup HTTP=%d cookies=%s", r0.status_code, cookies)
        time.sleep(2)

        # Direct bulletin
        url = f"https://www.farpost.ru/vladivostok/{FP_ID}.html"
        r1 = s.get(url, timeout=(10, 25), allow_redirects=True)
        final_url = r1.url
        ct = (r1.headers.get("Content-Type") or "").lower()
        if "1251" in ct or "cp1251" in ct:
            html = r1.content.decode("windows-1251", errors="replace")
        else:
            html = r1.text
        log.info("FP:direct HTTP=%d len=%d url=%s", r1.status_code, len(html), final_url[:80])

        prices = []
        # data-bulletin-price
        m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        if m:
            prices.append(int(m.group(1)))
        # JSON-LD
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.I):
            try:
                data = json.loads(m.group(1).strip())
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        offers = item.get("offers", {})
                        if isinstance(offers, dict):
                            offers = [offers]
                        for o in offers:
                            if isinstance(o, dict) and o.get("price"):
                                prices.append(float(o["price"]))
            except Exception:
                pass
        # viewbull-summary-price
        m = re.search(r'viewbull-summary-price__value[^>]*>([\d\s\u00a0]+)', html)
        if m:
            try:
                prices.append(float(m.group(1).replace(" ", "").replace("\xa0", "")))
            except Exception:
                pass
        # og:price
        m = re.search(r'property=["\']og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if m:
            prices.append(float(m.group(1)))

        is_verify = "/verify" in final_url or ("доска объявлений" in html[:3000].lower() and "bulletin" not in html[:3000].lower())
        ok = len(prices) > 0 and not is_verify
        results["FP:direct"] = {
            "ok": ok,
            "detail": f"HTTP={r1.status_code} len={len(html)} verify={is_verify} prices={prices[:5]} cookies={cookies}",
            "s": round(time.monotonic() - t, 1),
        }
        return ok
    except Exception as e:
        results["FP:direct"] = {"ok": False, "detail": f"error={e}", "s": round(time.monotonic() - t, 1)}
        return False

# === AVITO (quick) ===
def test_avito():
    t = time.monotonic()
    try:
        from curl_cffi import requests as cffi_req
        # Try a known listing
        url = "https://www.avito.ru/vladivostok/telefony-ADv8qA09qCq"
        resp = cffi_req.get(url, impersonate="chrome", timeout=15, headers={
            "Accept": "text/html",
            "Accept-Language": "ru-RU,ru;q=0.9",
        })
        html = resp.text
        captcha = ("captcha" in html.lower() or "hcaptcha" in html.lower()
                   or "challenge" in html.lower())
        prices = []
        for m in re.finditer(r'(\d[\d\s\u00a0]{2,9})\s*(?:\u20bd|руб)', html, re.I):
            try:
                v = float(m.group(1).replace(" ", "").replace("\xa0", ""))
                if 100 < v < 10000000:
                    prices.append(v)
            except Exception:
                pass
        ok = len(prices) > 0 and not captcha
        results["AV:quick"] = {
            "ok": ok,
            "detail": f"HTTP={resp.status_code} len={len(html)} captcha={captcha} prices={prices[:5]}",
            "s": round(time.monotonic() - t, 1),
        }
        return ok
    except Exception as e:
        results["AV:quick"] = {"ok": False, "detail": f"error={e}", "s": round(time.monotonic() - t, 1)}
        return False

# === RUN ===
log.info("=== diag_multi10 START ===")
test_ozon()
test_wb()
test_farpost()
test_avito()

total = round(time.monotonic() - t0, 1)
results["total_s"] = total
results["time"] = datetime.now().isoformat()

with open("d10_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

log.info("=== diag_multi10 DONE in %ss ===", total)
for k, v in results.items():
    if k not in ("total_s", "time"):
        log.info("  %-12s ok=%-5s %s", k, v.get("ok"), str(v.get("detail", ""))[:120])
