"""diag_fast_v2.py — Быстрый диагностик 4 платформ.
Каждая: 2-3 стратегии, curl_cffi, timeout 12s.
Run: C:\\Python315\\python.exe diag_fast_v2.py
"""
import json, logging, re, sys, time
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("DFV2")
R = {}
def rec(p, s, ok, d, dt):
    R[f"{p}:{s}"] = {"ok": ok, "detail": str(d)[:400], "s": round(dt, 1)}
    log.info("[%s] %s %s (%.1fs) %s", p, s, "OK" if ok else "FAIL", dt, str(d)[:120])

def now(): return datetime.now(timezone(timedelta(hours=10))).isoformat()

# ============================================================
# WB
# ============================================================
def wb_test():
    nm = 1097975275
    vol, part = nm // 100000, nm // 1000
    from curl_cffi import requests as cr
    H = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}

    # W1: card.json CDN
    t0 = time.time()
    try:
        path = f"/vol{vol}/part{part}/{nm}/info/ru/card.json"
        for host in [f"basket-{vol}.wbbasket.ru", f"basket-{vol+1}.wbbasket.ru", "basket-01.wbbasket.ru"]:
            url = f"https://{host}{path}"
            r = cr.get(url, impersonate="chrome", timeout=10, headers=H)
            if r.status_code == 200:
                d = r.json()
                title = d.get("imt_name", "")[:60]
                opts = len(d.get("options", []))
                rec("WB", "card_cdn", True, f"host={host} title={title} opts={opts}", time.time()-t0)
                with open("dfv2_wb_card.json", "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False)
                break
        else:
            rec("WB", "card_cdn", False, f"all hosts failed, last={r.status_code}", time.time()-t0)
    except Exception as e:
        rec("WB", "card_cdn", False, str(e)[:100], time.time()-t0)

    # W2: Price endpoints (fast, 2 best)
    t0 = time.time()
    price_ok = False
    for label, url in [
        ("card_v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"),
        ("catalog_v2", f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm}"),
        ("web_api", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm}"),
    ]:
        try:
            r = cr.get(url, impersonate="chrome", timeout=10, headers=H)
            if r.status_code == 200:
                d = r.json()
                prods = d.get("products") or d.get("data", {}).get("items", [])
                if prods:
                    p = prods[0]
                    sizes = p.get("sizes", [])
                    prices = sizes[0].get("price", {}) if sizes else {}
                    basic = prices.get("basic", 0)
                    product = prices.get("product", 0)
                    rec("WB", f"price_{label}", True, f"basic={basic} product={product} qty={sizes[0].get('qty') if sizes else '?'}", time.time()-t0)
                    price_ok = True
                    break
                elif "data" in d and isinstance(d["data"], dict):
                    dd = d["data"]
                    rec("WB", f"price_{label}", True, f"keys={list(dd.keys())[:8]}", time.time()-t0)
                    price_ok = True
                    break
        except Exception:
            continue
    if not price_ok:
        rec("WB", "price_all", False, f"last={r.status_code if 'r' in dir() else '?'}", time.time()-t0)

    # W3: HTML og:price
    t0 = time.time()
    try:
        url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        r = cr.get(url, impersonate="chrome", timeout=12)
        html = r.text
        prices = []
        m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html, re.I)
        if m: prices.append(f"og={m.group(1)}")
        m = re.search(r'"basic"\s*:\s*(\d+)', html)
        if m: prices.append(f"basic={m.group(1)}")
        vis = re.findall(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:120000])
        if vis: prices.append(f"vis={vis[0].replace(' ','')}")
        rec("WB", "html_price", len(prices) > 0, f"HTTP={r.status_code} len={len(html)} prices={prices[:4]}", time.time()-t0)
    except Exception as e:
        rec("WB", "html_price", False, str(e)[:100], time.time()-t0)

# ============================================================
# OZON
# ============================================================
def ozon_test():
    oid = "3481320800"
    from curl_cffi import requests as cr
    H = {"Accept": "application/json, text/html", "Referer": f"https://www.ozon.ru/product/{oid}/"}

    # O1: Composer API
    t0 = time.time()
    for url in [
        f"https://api.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{oid}/",
        f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{oid}/",
    ]:
        try:
            r = cr.get(url, impersonate="chrome", timeout=10, headers=H, verify=False)
            if r.status_code == 200:
                d = r.json()
                keys = list(d.keys())[:10]
                # Dig for price
                s = json.dumps(d, ensure_ascii=False)
                price_m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', s)
                rec("OZON", "composer_api", True, f"url={url.split('/')[2]} keys={keys} price={price_m.group(1) if price_m else '?'}", time.time()-t0)
                with open("dfv2_ozon_composer.json", "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False)
                return
        except Exception:
            continue
    rec("OZON", "composer_api", False, f"last={r.status_code if 'r' in dir() else 'err'}", time.time()-t0)

    # O2: HTML + JSON-LD
    t0 = time.time()
    try:
        url = f"https://www.ozon.ru/product/{oid}/"
        r = cr.get(url, impersonate="chrome", timeout=12, headers={"Accept": "text/html", "Referer": "https://www.ozon.ru/"})
        html = r.text
        # JSON-LD
        ld = None
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S):
            try:
                d = json.loads(m.group(1))
                if isinstance(d, dict) and d.get("@type") == "Product":
                    ld = d; break
                if isinstance(d, list):
                    for item in d:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            ld = item; break
            except: pass
        if ld:
            offers = ld.get("offers", {})
            if isinstance(offers, list): offers = offers[0] if offers else {}
            price = offers.get("price", "?")
            name = ld.get("name", "")[:50]
            rec("OZON", "html_jsonld", True, f"HTTP={r.status_code} name={name} price={price} imgs={len(ld.get('image',[]))}", time.time()-t0)
        else:
            # Fallback: visible price
            pm = re.findall(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:100000])
            rec("OZON", "html_jsonld", len(pm) > 0, f"HTTP={r.status_code} len={len(html)} no-LD vis_prices={pm[:3]}", time.time()-t0)
    except Exception as e:
        rec("OZON", "html_jsonld", False, str(e)[:100], time.time()-t0)

# ============================================================
# FARPOST
# ============================================================
def farpost_test():
    lid = "138859452"
    from curl_cffi import requests as cr
    H = {"Accept": "text/html", "Referer": "https://www.farpost.ru/"}

    # F1: Direct HTML
    t0 = time.time()
    try:
        url = f"https://www.farpost.ru/vladivostok/{lid}.html"
        r = cr.get(url, impersonate="chrome", timeout=12, headers=H)
        # Encoding
        ct = (r.headers.get("Content-Type") or "").lower()
        if "1251" in ct or "cp1251" in ct:
            html = r.content.decode("windows-1251", errors="replace")
        else:
            html = r.text
        # Price
        prices = []
        m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        if m: prices.append(f"attr={m.group(1)}")
        # JSON-LD
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S):
            try:
                d = json.loads(m.group(1))
                if isinstance(d, dict) and d.get("@type") == "Product":
                    offers = d.get("offers", {})
                    if isinstance(offers, list): offers = offers[0] if offers else {}
                    p = offers.get("price", "")
                    if p: prices.append(f"ld={p}")
                    nm = d.get("name", "")[:50]
                    rec("FARPOST", "html", True, f"HTTP={r.status_code} len={len(html)} name={nm} prices={prices}", time.time()-t0)
                    with open("dfv2_fp_html.txt", "w", encoding="utf-8") as f:
                        f.write(html[:5000])
                    return
            except: pass
        # Visible price
        vm = re.findall(r'>(\d[\d\s]{2,10})\s*(?:₽|руб)', html[:80000])
        rec("FARPOST", "html", len(vm) > 0, f"HTTP={r.status_code} len={len(html)} vis={vm[:3]}", time.time()-t0)
    except Exception as e:
        rec("FARPOST", "html", False, str(e)[:100], time.time()-t0)

    # F2: Second listing
    t0 = time.time()
    try:
        lid2 = "138952176"
        url2 = f"https://www.farpost.ru/vladivostok/{lid2}.html"
        r = cr.get(url2, impersonate="chrome", timeout=12, headers=H)
        html = r.content.decode("windows-1251", errors="replace") if "1251" in (r.headers.get("Content-Type") or "").lower() else r.text
        m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        pm = re.findall(r'>(\d[\d\s]{2,10})\s*(?:₽|руб)', html[:80000])
        rec("FARPOST", "html2", r.status_code == 200, f"lid={lid2} HTTP={r.status_code} attr={m.group(1) if m else '?'} vis={pm[:2]}", time.time()-t0)
    except Exception as e:
        rec("FARPOST", "html2", False, str(e)[:100], time.time()-t0)

# ============================================================
# AVITO
# ============================================================
def avito_test():
    aid = "1045231001"
    from curl_cffi import requests as cr
    H = {"Accept": "text/html", "Referer": "https://www.avito.ru/"}

    # A1: Desktop HTML
    t0 = time.time()
    try:
        url = f"https://www.avito.ru/moskva/ad-{aid}"
        r = cr.get(url, impersonate="chrome", timeout=12, headers=H)
        html = r.text
        # JSON-LD
        ld = None
        for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S):
            try:
                d = json.loads(m.group(1))
                if isinstance(d, dict) and d.get("@type") in ("Product", "Offer", "IndividualProduct"):
                    ld = d; break
            except: pass
        # Visible
        pm = re.findall(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:100000])
        title_m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        rec("AVITO", "html_desktop", r.status_code == 200 and (ld or pm),
            f"HTTP={r.status_code} len={len(html)} ld={bool(ld)} title={title_m.group(1)[:40] if title_m else '?'} prices={pm[:3]}", time.time()-t0)
    except Exception as e:
        rec("AVITO", "html_desktop", False, str(e)[:100], time.time()-t0)

    # A2: Mobile
    t0 = time.time()
    try:
        url = f"https://m.avito.ru/moskva/ad-{aid}"
        r = cr.get(url, impersonate="chrome", timeout=12,
                   headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"})
        html = r.text
        pm = re.findall(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:80000])
        title_m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        rec("AVITO", "html_mobile", r.status_code == 200 and (pm or title_m),
            f"HTTP={r.status_code} len={len(html)} title={title_m.group(1)[:40] if title_m else '?'} prices={pm[:3]}", time.time()-t0)
    except Exception as e:
        rec("AVITO", "html_mobile", False, str(e)[:100], time.time()-t0)

# ============================================================
def main():
    t0 = time.time()
    log.info("DFV2 START %s", now())
    wb_test()
    ozon_test()
    farpost_test()
    avito_test()
    dt = time.time() - t0
    log.info("DFV2 DONE %.1fs", dt)
    out = {"time": now(), "total_s": round(dt, 1), "results": R}
    with open("dfv2_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    log.info("Saved dfv2_results.json")
    for p in ("WB", "OZON", "FARPOST", "AVITO"):
        ok = sum(1 for k, v in R.items() if k.startswith(p + ":") and v["ok"])
        tot = sum(1 for k in R if k.startswith(p + ":"))
        log.info("  %-8s %d/%d OK", p, ok, tot)

if __name__ == "__main__":
    main()
