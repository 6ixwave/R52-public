"""diag27: 12 коротких стратегий по 4 площадкам. Цель — найти хотя бы 1 рабочую на каждой."""
import json, re, time, sys, os
from datetime import datetime, timezone, timedelta

os.chdir(os.path.dirname(os.path.abspath(__file__)))
results = {}
t0 = time.monotonic()

def log(msg):
    print(f"[{time.monotonic()-t0:.1f}s] {msg}", flush=True)

def mark(name, ok, detail=""):
    results[name] = {"ok": ok, "detail": detail, "s": round(time.monotonic()-t0,1)}
    log(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")

# ============================================================
# CFFI
# ============================================================
try:
    from curl_cffi import requests as cffi_req
    CFFI = True
except ImportError:
    CFFI = False
    log("curl_cffi NOT available")

import requests as req

UA_DESK = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
UA_MOB  = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"

# ============================================================
# FARPOST
# ============================================================
log("=== FARPOST ===")

# FP-1: Card 134693091 via CFFI (previously confirmed JSON-LD)
try:
    if CFFI:
        r = cffi_req.get("https://www.farpost.ru/vladivostok/134693091.html",
                         impersonate="chrome", timeout=15,
                         headers={"Accept-Language":"ru-RU,ru;q=0.9"})
        html = r.content.decode("windows-1251", errors="replace") if "1251" in (r.headers.get("Content-Type","")) else r.text
        jld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        price = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        title_m = re.search(r'<h1[^>]*>.*?<span[^>]*data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
        has_bull = "bulletin" in html.lower() and "viewbull" in html.lower()
        mark("FP:card_cffi", r.status_code==200 and (jld or price),
             f"HTTP={r.status_code} len={len(html)} jld={len(jld)} price={'Y:'+price.group(1) if price else 'N'} bull={has_bull}")
    else:
        mark("FP:card_cffi", False, "no cffi")
except Exception as e:
    mark("FP:card_cffi", False, str(e)[:80])

# FP-2: Category with warm-up (homepage first for cookies)
try:
    if CFFI:
        s = cffi_req.Session(impersonate="chrome")
        s.get("https://www.farpost.ru/", timeout=10)
        time.sleep(1.5)
        r2 = s.get("https://www.farpost.ru/vladivostok/auto/misc/", timeout=15,
                   headers={"Accept-Language":"ru-RU,ru;q=0.9","Referer":"https://www.farpost.ru/"})
        html2 = r2.content.decode("windows-1251", errors="replace") if "1251" in (r2.headers.get("Content-Type","")) else r2.text
        # Count bulletin links: /vladivostok/...-{digits}.html
        bull_links = re.findall(r'href="[^"]*?/(\d{5,})\.html"', html2)
        prices_found = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html2)
        # New structure check: data-bulletin-id
        bull_ids = re.findall(r'data-bulletin-id=["\'](\d+)["\']', html2)
        # Check for "verify" in final URL
        is_verify = "/verify" in (r2.url or "")
        mark("FP:cat_warmup", len(bull_links)>0 or len(prices_found)>0 or len(bull_ids)>0,
             f"HTTP={r2.status_code} len={len(html2)} links={len(bull_links)} prices={len(prices_found)} ids={len(bull_ids)} verify={is_verify} url={r2.url[:60]}")
except Exception as e:
    mark("FP:cat_warmup", False, str(e)[:80])

# FP-3: Category with mobile UA (m.farpost.ru)
try:
    if CFFI:
        r3 = cffi_req.get("https://m.farpost.ru/vladivostok/auto/misc/",
                          impersonate="chrome", timeout=15,
                          headers={"User-Agent":UA_MOB,"Accept-Language":"ru-RU,ru;q=0.9"})
        html3 = r3.content.decode("windows-1251", errors="replace") if "1251" in (r3.headers.get("Content-Type","")) else r3.text
        bull3 = re.findall(r'/(\d{5,})\.html', html3)
        prices3 = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html3)
        # Check JSON in scripts
        scripts3 = re.findall(r'<script[^>]*>(.*?)</script>', html3, re.DOTALL)
        data_scripts = [s for s in scripts3 if '"bulletin"' in s or '"items"' in s or 'lightbox' in s]
        is_verify3 = "/verify" in (r3.url or "")
        mark("FP:cat_mobile", len(bull3)>3 or len(prices3)>0,
             f"HTTP={r3.status_code} len={len(html3)} bull={len(bull3)} prices={len(prices3)} scripts={len(data_scripts)} verify={is_verify3}")
except Exception as e:
    mark("FP:cat_mobile", False, str(e)[:80])

# FP-4: Card via Playwright (real browser, renders JS)
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        pg = br.new_page(user_agent=UA_DESK, locale="ru-RU")
        pg.goto("https://www.farpost.ru/vladivostok/134693091.html", wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(3000)
        html4 = pg.content()
        jld4 = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html4, re.DOTALL)
        price4 = re.search(r'data-bulletin-price=["\'](\d+)["\']', html4)
        title4 = re.search(r'<h1[^>]*>.*?<span[^>]*data-field="subject"[^>]*>(.*?)</span>', html4, re.DOTALL)
        br.close()
        mark("FP:card_pw", len(jld4)>0 or (price4 is not None),
             f"len={len(html4)} jld={len(jld4)} price={'Y:'+price4.group(1) if price4 else 'N'}")
except Exception as e:
    mark("FP:card_pw", False, str(e)[:80])

# FP-5: Category via Playwright
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        pg = br.new_page(user_agent=UA_DESK, locale="ru-RU")
        # Warm: visit homepage
        pg.goto("https://www.farpost.ru/", wait_until="domcontentloaded", timeout=15000)
        pg.wait_for_timeout(2000)
        pg.goto("https://www.farpost.ru/vladivostok/auto/misc/", wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(4000)
        html5 = pg.content()
        bull5 = re.findall(r'href="[^"]*?/(\d{5,})\.html"', html5)
        prices5 = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html5)
        is_verify5 = "/verify" in pg.url
        br.close()
        mark("FP:cat_pw", len(bull5)>3 or len(prices5)>0,
             f"len={len(html5)} bull={len(bull5)} prices={len(prices5)} verify={is_verify5} url={pg.url[:60]}")
except Exception as e:
    mark("FP:cat_pw", False, str(e)[:80])

# ============================================================
# OZON
# ============================================================
log("=== OZON ===")
OZON_ID = "3481320800"
OZON_URL = f"https://www.ozon.ru/product/{OZON_ID}/"

# OZ-1: Playwright PDP
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        pg = br.new_page(user_agent=UA_DESK, locale="ru-RU")
        pg.goto(OZON_URL, wait_until="domcontentloaded", timeout=25000)
        pg.wait_for_timeout(5000)
        html_oz = pg.content()
        # Check for price in rendered DOM
        price_oz = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*(?:₽|руб)', html_oz)
        jld_oz = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_oz, re.DOTALL)
        og_price = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html_oz)
        has_widget = 'data-widget' in html_oz
        br.close()
        mark("OZ:pw_pdp", len(price_oz)>0 or len(jld_oz)>0 or og_price,
             f"len={len(html_oz)} prices={len(price_oz)} jld={len(jld_oz)} og_price={og_price.group(1) if og_price else 'N'} widget={has_widget}")
except Exception as e:
    mark("OZ:pw_pdp", False, str(e)[:80])

# OZ-2: Composer API v2 with full path
try:
    if CFFI:
        ep = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{OZON_ID}/"
        r_oz = cffi_req.get(ep, impersonate="chrome", timeout=15,
                           headers={"Accept":"application/json","Accept-Language":"ru-RU,ru;q=0.9",
                                    "Referer":"https://www.ozon.ru/"})
        if r_oz.status_code == 200:
            data = r_oz.json()
            # Navigate to find price
            blocks = data.get("page",{}).get("blocks",[])
            price_found = False
            for b in blocks:
                if b.get("type") in ("webPrice","price","pdp-price","priceV2"):
                    price_found = True
                    break
            mark("OZ:composer_v2", price_found, f"HTTP=200 blocks={len(blocks)} price_widget={price_found}")
        else:
            mark("OZ:composer_v2", False, f"HTTP={r_oz.status_code} len={len(r_oz.content)}")
except Exception as e:
    mark("OZ:composer_v2", False, str(e)[:80])

# OZ-3: Mobile API (api.ozon.ru with mobile UA)
try:
    if CFFI:
        r_oz3 = cffi_req.get(f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{OZON_ID}/",
                             impersonate="chrome", timeout=15,
                             headers={"User-Agent":UA_MOB,"Accept":"application/json",
                                      "X-Platform":"mobile","Accept-Language":"ru-RU"})
        ok3 = r_oz3.status_code==200
        detail3 = f"HTTP={r_oz3.status_code} len={len(r_oz3.content)}"
        if ok3:
            d3 = r_oz3.json()
            detail3 += f" keys={list(d3.keys())[:5]}"
        mark("OZ:mobile_api", ok3, detail3)
except Exception as e:
    mark("OZ:mobile_api", False, str(e)[:80])

# OZ-4: PDP entrypoint (different endpoint)
try:
    if CFFI:
        r_oz4 = cffi_req.get(f"https://www.ozon.ru/api/entrypoint/pdp/{OZON_ID}",
                             impersonate="chrome", timeout=15,
                             headers={"Accept":"application/json","Accept-Language":"ru-RU",
                                      "Referer":OZON_URL})
        ok4 = r_oz4.status_code==200
        mark("OZ:pdp_entry", ok4, f"HTTP={r_oz4.status_code} len={len(r_oz4.content)}")
except Exception as e:
    mark("OZ:pdp_entry", False, str(e)[:80])

# ============================================================
# WILDBERRIES
# ============================================================
log("=== WILDBERRIES ===")
WB_NM = 1097975275

# WB-1: Playwright HTML page
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        pg = br.new_page(user_agent=UA_DESK, locale="ru-RU")
        pg.goto(f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx",
                wait_until="domcontentloaded", timeout=25000)
        pg.wait_for_timeout(4000)
        html_wb = pg.content()
        price_wb = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*(?:₽|руб)', html_wb)
        og_p = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html_wb)
        jld_wb = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_wb, re.DOTALL)
        br.close()
        mark("WB:pw_html", len(price_wb)>0 or og_p or len(jld_wb)>0,
             f"len={len(html_wb)} prices={len(price_wb)} og={og_p.group(1) if og_p else 'N'} jld={len(jld_wb)}")
except Exception as e:
    mark("WB:pw_html", False, str(e)[:80])

# WB-2: Basket CDN (recalculate vol/part)
try:
    if CFFI:
        vol = WB_NM // 100000
        part = WB_NM // 1000
        path = f"/vol{vol}/part{part}/{WB_NM}/info/ru/card.json"
        # Try a few hosts
        found = False
        for n in [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]:
            host = f"basket-{n:02d}.wbbasket.ru"
            r_wb = cffi_req.get(f"https://{host}{path}", impersonate="chrome", timeout=8,
                               headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"})
            if r_wb.status_code == 200:
                d = r_wb.json()
                if d.get("nm_id"):
                    found = True
                    mark("WB:basket_cdn", True, f"host={host} nm_id={d['nm_id']} name={str(d.get('imt_name',''))[:40]}")
                    break
        if not found:
            mark("WB:basket_cdn", False, f"vol={vol} part={part} all 12 hosts failed")
except Exception as e:
    mark("WB:basket_cdn", False, str(e)[:80])

# WB-3: Price API (card.wb.ru v1)
try:
    if CFFI:
        r_wb3 = cffi_req.get(
            f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}",
            impersonate="chrome", timeout=12,
            headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"})
        ok3 = r_wb3.status_code==200
        mark("WB:price_v1", ok3, f"HTTP={r_wb3.status_code} len={len(r_wb3.content)}")
        if ok3:
            d3 = r_wb3.json()
            prods = d3.get("data",{}).get("items") or d3.get("products") or []
            if prods:
                p = prods[0]
                mark("WB:price_v1_data", True, f"name={str(p.get('name',''))[:40]} sizes={len(p.get('sizes',[]))}")
except Exception as e:
    mark("WB:price_v1", False, str(e)[:80])

# ============================================================
# AVITO
# ============================================================
log("=== AVITO ===")

# AV-1: RSS feed
try:
    if CFFI:
        r_av = cffi_req.get("https://www.avito.ru/rss/vladivostok/transport/avtomobili/",
                           impersonate="chrome", timeout=15,
                           headers={"Accept":"application/rss+xml,application/xml","Accept-Language":"ru-RU"})
        items = re.findall(r'<item>', r_av.text)
        prices = re.findall(r'<price>(\d+)</price>', r_av.text)
        mark("AV:rss", r_av.status_code==200 and len(items)>0,
             f"HTTP={r_av.status_code} items={len(items)} prices={len(prices)} len={len(r_av.content)}")
except Exception as e:
    mark("AV:rss", False, str(e)[:80])

# AV-2: Category page via CFFI (different path)
try:
    if CFFI:
        r_av2 = cffi_req.get("https://www.avito.ru/vladivostok/transport/avtomobili",
                             impersonate="chrome", timeout=15,
                             headers={"Accept":"text/html","Accept-Language":"ru-RU"})
        html_av = r_av2.text
        # Avito uses data-autoprefixer or specific classes
        offers = re.findall(r'data-autoprefixer="(\d+)"', html_av)
        prices_av = re.findall(r'"price"\s*:\s*(\d+)', html_av)
        # New structure: <a class="css-..." href="/vladivostok/...">
        links_av = re.findall(r'href="/vladivostok/[^"]+/\d+"', html_av)
        mark("AV:cat_cffi", r_av2.status_code==200 and (len(offers)>0 or len(prices_av)>0 or len(links_av)>5),
             f"HTTP={r_av2.status_code} len={len(html_av)} offers={len(offers)} prices={len(prices_av)} links={len(links_av)}")
except Exception as e:
    mark("AV:cat_cffi", False, str(e)[:80])

# AV-3: Playwright category
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        pg = br.new_page(user_agent=UA_DESK, locale="ru-RU")
        pg.goto("https://www.avito.ru/vladivostok/transport/avtomobili",
                wait_until="domcontentloaded", timeout=25000)
        pg.wait_for_timeout(5000)
        html_av3 = pg.content()
        offers3 = re.findall(r'data-autoprefixer="(\d+)"', html_av3)
        prices3 = re.findall(r'(\d[\d\s\u00a0]{2,10})\s*₽', html_av3)
        links3 = re.findall(r'href="/vladivostok/[^"]+/\d+"', html_av3)
        br.close()
        mark("AV:cat_pw", len(offers3)>0 or len(prices3)>3 or len(links3)>5,
             f"len={len(html_av3)} offers={len(offers3)} prices={len(prices3)} links={len(links3)}")
except Exception as e:
    mark("AV:cat_pw", False, str(e)[:80])

# ============================================================
# SAVE
# ============================================================
results["_meta"] = {"total_s": round(time.monotonic()-t0,1),
                    "time": datetime.now(timezone(timedelta(hours=10))).isoformat()}
with open("d27_results.json","w",encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
log(f"DONE {time.monotonic()-t0:.1f}s -> d27_results.json")
