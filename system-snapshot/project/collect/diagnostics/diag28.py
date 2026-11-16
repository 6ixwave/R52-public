"""diag28: 14 стратегий. Фокус: FP структура, OZ SSL, WB CDN, AV warmup."""
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

try:
    from curl_cffi import requests as cffi_req
    CFFI = True
except ImportError:
    CFFI = False

import requests as req
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# ============================================================
# FARPOST
# ============================================================
log("=== FARPOST ===")

# FP-A: Dump card page structure — что реально в 198KB?
try:
    if CFFI:
        r = cffi_req.get("https://www.farpost.ru/vladivostok/134693091.html",
                         impersonate="chrome", timeout=15,
                         headers={"Accept-Language":"ru-RU,ru;q=0.9"})
        html = r.content.decode("windows-1251", errors="replace")
        # Key markers
        markers = {
            "h1": bool(re.search(r'<h1', html, re.I)),
            "data-field": len(re.findall(r'data-field=', html)),
            "viewbull": len(re.findall(r'viewbull', html, re.I)),
            "bulletin": len(re.findall(r'bulletin', html, re.I)),
            "price": len(re.findall(r'price', html, re.I)),
            "og_title": bool(re.search(r'og:title', html, re.I)),
            "jsonld": len(re.findall(r'ld\+json', html, re.I)),
            "script_count": len(re.findall(r'<script', html, re.I)),
            "img_count": len(re.findall(r'<img', html, re.I)),
            "ajax": len(re.findall(r'ajax|fetch|XMLHttp', html, re.I)),
            "nuxt": len(re.findall(r'nuxt|__NUXT__', html, re.I)),
            "react": len(re.findall(r'react|__NEXT', html, re.I)),
            "verify": "/verify" in html,
            "cookie_banner": "cookie" in html.lower()[:3000],
        }
        # First 500 chars of body
        body_m = re.search(r'<body[^>]*>(.{0,500})', html, re.DOTALL)
        body_start = body_m.group(1)[:300] if body_m else "NO BODY"
        # Title
        title_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.I|re.DOTALL)
        title = title_m.group(1).strip()[:80] if title_m else "?"
        mark("FP:card_dump", True, f"len={len(html)} title='{title}' markers={markers}")
        # Save sample for inspection
        with open("d28_fp_card_sample.txt","w",encoding="utf-8") as f:
            f.write(f"TITLE: {title}\nURL: {r.url}\nSTATUS: {r.status_code}\nBODY_START:\n{body_start}\n\nMARKERS: {json.dumps(markers)}\n\nFIRST_3000:\n{html[:3000]}\n\nLAST_1000:\n{html[-1000:]}")
    else:
        mark("FP:card_dump", False, "no cffi")
except Exception as e:
    mark("FP:card_dump", False, str(e)[:100])

# FP-B: Category 2-step warmup via cffi Session (homepage 3s wait then category)
try:
    if CFFI:
        s = cffi_req.Session(impersonate="chrome")
        s.get("https://www.farpost.ru/", timeout=10)
        time.sleep(3)
        r2 = s.get("https://www.farpost.ru/vladivostok/auto/misc/", timeout=15,
                   headers={"Accept-Language":"ru-RU,ru;q=0.9","Referer":"https://www.farpost.ru/"})
        html2 = r2.content.decode("windows-1251", errors="replace")
        verify = "/verify" in (r2.url or "")
        bull_links = re.findall(r'href="[^"]*?/(\d{5,})\.html"', html2)
        # New: data-bulletin-id, data-id, or just /{city}/...-{id}.html
        all_ids = re.findall(r'/(\d{6,})\.html', html2)
        # Check for "listing" or "item" blocks
        items = re.findall(r'class="[^"]*(?:listing|item|bulletin|card)[^"]*"', html2, re.I)
        mark("FP:cat_2step", len(all_ids)>3 and not verify,
             f"HTTP={r2.status_code} len={len(html2)} verify={verify} ids={len(all_ids)} items={len(items)} url={r2.url[:60]}")
except Exception as e:
    mark("FP:cat_2step", False, str(e)[:80])

# FP-C: Card via cffi with Referer=homepage (simulate navigation)
try:
    if CFFI:
        s = cffi_req.Session(impersonate="chrome")
        s.get("https://www.farpost.ru/", timeout=10)
        time.sleep(2)
        r3 = s.get("https://www.farpost.ru/vladivostok/134693091.html", timeout=15,
                   headers={"Accept-Language":"ru-RU,ru;q=0.9",
                            "Referer":"https://www.farpost.ru/vladivostok/auto/misc/"})
        html3 = r3.content.decode("windows-1251", errors="replace")
        # Look for ANY price pattern
        prices = re.findall(r'(\d{3,})\s*(?:₽|руб)', html3)
        # Look for "subject" or "model" in data-field
        subj = re.findall(r'data-field="[^"]*(?:subject|model|name)[^"]*"[^>]*>(.*?)</', html3, re.DOTALL)
        # Check for "data-bulletin"
        bull_attrs = re.findall(r'data-bulletin-[\w]+', html3)
        # Check for new structure: "data-attribute" patterns
        data_attrs = re.findall(r'data-([\w-]+)=', html3)
        data_attr_set = list(set(data_attrs))[:20]
        mark("FP:card_referer", len(prices)>0 or len(subj)>0,
             f"len={len(html3)} prices={prices[:3]} subj={len(subj)} bull_attrs={bull_attrs[:5]} data_attrs={data_attr_set}")
except Exception as e:
    mark("FP:card_referer", False, str(e)[:80])

# FP-D: Farpost AJAX/API endpoint (internal)
try:
    if CFFI:
        # Try internal API: /ajax/bulletin/... or /api/...
        endpoints = [
            f"https://www.farpost.ru/ajax/bulletin/134693091/",
            f"https://www.farpost.ru/api/v1/bulletin/134693091",
            f"https://www.farpost.ru/vladivostok/134693091/?format=json",
        ]
        found = False
        for ep in endpoints:
            r4 = cffi_req.get(ep, impersonate="chrome", timeout=10,
                             headers={"Accept":"application/json","Accept-Language":"ru-RU",
                                      "Referer":"https://www.farpost.ru/"})
            ct = r4.headers.get("Content-Type","")
            if r4.status_code == 200 and "json" in ct:
                found = True
                mark("FP:ajax_api", True, f"ep={ep} ct={ct} len={len(r4.content)}")
                break
            elif r4.status_code == 200 and len(r4.content) > 100:
                # Maybe HTML but different
                if not found:
                    mark("FP:ajax_api", False, f"ep={ep} HTTP=200 ct={ct} len={len(r4.content)} (not json)")
        if not found:
            mark("FP:ajax_api", False, "all endpoints non-JSON or 404")
except Exception as e:
    mark("FP:ajax_api", False, str(e)[:80])

# ============================================================
# OZON
# ============================================================
log("=== OZON ===")
OZON_ID = "3481320800"

# OZ-A: Composer API via standard requests (avoids cffi SSL issue)
try:
    ep = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{OZON_ID}/"
    h = {"Accept":"application/json","Accept-Language":"ru-RU,ru;q=0.9",
         "Referer":"https://www.ozon.ru/","User-Agent":UA}
    r_oz = req.get(ep, headers=h, timeout=15)
    ok = r_oz.status_code == 200
    detail = f"HTTP={r_oz.status_code} len={len(r_oz.content)}"
    if ok:
        d = r_oz.json()
        blocks = d.get("page",{}).get("blocks",[])
        price_blocks = [b for b in blocks if isinstance(b,dict) and "price" in (b.get("type","").lower())]
        detail += f" blocks={len(blocks)} price_blocks={len(price_blocks)}"
        # Try to find title
        title = d.get("page",{}).get("title") or d.get("page",{}).get("seoTitle","")
        detail += f" title='{str(title)[:40]}'"
    mark("OZ:composer_req", ok, detail)
except Exception as e:
    mark("OZ:composer_req", False, str(e)[:100])

# OZ-B: Composer API via cffi verify=False
try:
    if CFFI:
        ep = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{OZON_ID}/"
        r_oz2 = cffi_req.get(ep, impersonate="chrome", timeout=15, verify=False,
                            headers={"Accept":"application/json","Accept-Language":"ru-RU",
                                     "Referer":"https://www.ozon.ru/"})
        ok2 = r_oz2.status_code == 200
        detail2 = f"HTTP={r_oz2.status_code} len={len(r_oz2.content)}"
        if ok2:
            d2 = r_oz2.json()
            blocks2 = d2.get("page",{}).get("blocks",[])
            detail2 += f" blocks={len(blocks2)}"
        mark("OZ:composer_cffi_nv", ok2, detail2)
    else:
        mark("OZ:composer_cffi_nv", False, "no cffi")
except Exception as e:
    mark("OZ:composer_cffi_nv", False, str(e)[:100])

# OZ-C: PW with networkidle + 8s wait + mobile viewport
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
        pg = br.new_context(viewport={"width":390,"height":844}, locale="ru-RU",
                           user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")
        pg2 = pg.new_page()
        pg2.goto(f"https://www.ozon.ru/product/{OZON_ID}/", wait_until="networkidle", timeout=30000)
        pg2.wait_for_timeout(8000)
        html_oz = pg2.content()
        prices = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*(?:₽|руб)', html_oz)
        og_p = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html_oz)
        jld = re.findall(r'ld\+json', html_oz)
        # Check if it's a "loading" shell
        has_skeleton = "skeleton" in html_oz.lower() or "loading" in html_oz.lower()
        br.close()
        mark("OZ:pw_mobile", len(prices)>0 or og_p or len(jld)>0,
             f"len={len(html_oz)} prices={len(prices)} og={og_p.group(1) if og_p else 'N'} jld={len(jld)} skeleton={has_skeleton}")
except Exception as e:
    mark("OZ:pw_mobile", False, str(e)[:100])

# OZ-D: www.ozon.ru composer (different host than api.ozon.ru)
try:
    ep = f"https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{OZON_ID}/"
    h = {"Accept":"application/json","Accept-Language":"ru-RU,ru;q=0.9",
         "Referer":f"https://www.ozon.ru/product/{OZON_ID}/","User-Agent":UA}
    r_oz4 = req.get(ep, headers=h, timeout=15)
    ok4 = r_oz4.status_code == 200
    detail4 = f"HTTP={r_oz4.status_code} len={len(r_oz4.content)}"
    if ok4:
        try:
            d4 = r_oz4.json()
            detail4 += f" keys={list(d4.keys())[:5]}"
        except:
            detail4 += f" (not json) head={r_oz4.text[:100]}"
    mark("OZ:composer_www", ok4, detail4)
except Exception as e:
    mark("OZ:composer_www", False, str(e)[:100])

# ============================================================
# WILDBERRIES
# ============================================================
log("=== WILDBERRIES ===")
WB_NM = 1097975275

# WB-A: Basket CDN — scan all 60 hosts with short timeout
try:
    if CFFI:
        vol = WB_NM // 100000
        part = WB_NM // 1000
        path = f"/vol{vol}/part{part}/{WB_NM}/info/ru/card.json"
        found_host = None
        for n in range(1, 61):
            host = f"basket-{n:02d}.wbbasket.ru"
            try:
                r_wb = cffi_req.get(f"https://{host}{path}", impersonate="chrome", timeout=5,
                                   headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/"})
                if r_wb.status_code == 200:
                    d = r_wb.json()
                    if d.get("nm_id"):
                        found_host = host
                        mark("WB:basket_60", True, f"host={host} nm={d['nm_id']} name={str(d.get('imt_name',''))[:40]}")
                        break
            except:
                continue
        if not found_host:
            # Maybe product doesn't exist — try a known valid one
            test_nm = 1000000000
            vol2 = test_nm // 100000
            part2 = test_nm // 1000
            path2 = f"/vol{vol2}/part{part2}/{test_nm}/info/ru/card.json"
            for n in [1,5,10,20,30,40,50,60]:
                host = f"basket-{n:02d}.wbbasket.ru"
                try:
                    r_t = cffi_req.get(f"https://{host}{path2}", impersonate="chrome", timeout=5,
                                      headers={"Accept":"application/json"})
                    if r_t.status_code == 200:
                        mark("WB:basket_60", False, f"target vol={vol} part={part} all fail; test nm={test_nm} vol={vol2} -> {host} 200")
                        break
                except:
                    pass
            else:
                mark("WB:basket_60", False, f"vol={vol} part={part} all 60 fail + test also fail")
    else:
        mark("WB:basket_60", False, "no cffi")
except Exception as e:
    mark("WB:basket_60", False, str(e)[:80])

# WB-B: Price via catalog.wb.ru (mirror)
try:
    if CFFI:
        urls = [
            f"https://catalog.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}",
            f"https://catalog.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}",
            f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}",
        ]
        for u in urls:
            r_wb2 = cffi_req.get(u, impersonate="chrome", timeout=10,
                                headers={"Accept":"application/json","Referer":"https://www.wildberries.ru/",
                                         "Origin":"https://www.wildberries.ru","User-Agent":UA})
            if r_wb2.status_code == 200:
                d = r_wb2.json()
                prods = d.get("data",{}).get("items") or d.get("products") or []
                if prods:
                    p = prods[0]
                    mark("WB:price_mirror", True, f"url={u.split('/')[2]} name={str(p.get('name',''))[:40]} sizes={len(p.get('sizes',[]))}")
                    break
            else:
                if "catalog" in u:
                    mark("WB:price_mirror", False, f"catalog.wb.ru v2 HTTP={r_wb2.status_code}")
        else:
            mark("WB:price_mirror", False, f"all 3 endpoints failed (last HTTP={r_wb2.status_code})")
except Exception as e:
    mark("WB:price_mirror", False, str(e)[:80])

# WB-C: PW with wait_until="load" + 6s
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
        pg = br.new_context(viewport={"width":1920,"height":1080}, locale="ru-RU", user_agent=UA)
        pg2 = pg.new_page()
        pg2.goto(f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx",
                wait_until="load", timeout=30000)
        pg2.wait_for_timeout(6000)
        html_wb = pg2.content()
        prices = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*(?:₽|руб)', html_wb)
        og_p = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html_wb)
        jld = re.findall(r'ld\+json', html_wb)
        br.close()
        mark("WB:pw_load", len(prices)>0 or og_p or len(jld)>0,
             f"len={len(html_wb)} prices={len(prices)} og={og_p.group(1) if og_p else 'N'} jld={len(jld)}")
except Exception as e:
    mark("WB:pw_load", False, str(e)[:100])

# ============================================================
# AVITO
# ============================================================
log("=== AVITO ===")

# AV-A: 2-step warmup (homepage → wait → category) via cffi Session
try:
    if CFFI:
        s = cffi_req.Session(impersonate="chrome")
        # Step 1: homepage
        r1 = s.get("https://www.avito.ru/", timeout=15)
        time.sleep(3)
        # Step 2: category
        r_av = s.get("https://www.avito.ru/vladivostok/transport/avtomobili", timeout=15,
                    headers={"Accept":"text/html","Accept-Language":"ru-RU",
                             "Referer":"https://www.avito.ru/"})
        html_av = r_av.text
        offers = re.findall(r'data-autoprefixer="(\d+)"', html_av)
        prices = re.findall(r'"price"\s*:\s*(\d+)', html_av)
        links = re.findall(r'href="/vladivostok/[^"]+/\d+"', html_av)
        # Check for consent/cookie wall
        consent = "consent" in html_av.lower() or "cookie" in html_av.lower()[:2000]
        # New: data-attribute patterns
        data_items = re.findall(r'data-([\w-]+)="[^"]*"', html_av[:5000])
        mark("AV:warmup_cat", r_av.status_code==200 and (len(offers)>0 or len(prices)>0 or len(links)>5),
             f"HTTP={r_av.status_code} len={len(html_av)} offers={len(offers)} prices={len(prices)} links={len(links)} consent={consent}")
except Exception as e:
    mark("AV:warmup_cat", False, str(e)[:80])

# AV-B: PW with consent click + longer wait
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
        pg = br.new_context(viewport={"width":1920,"height":1080}, locale="ru-RU", user_agent=UA)
        pg2 = pg.new_page()
        pg2.goto("https://www.avito.ru/vladivostok/transport/avtomobili",
                wait_until="domcontentloaded", timeout=25000)
        pg2.wait_for_timeout(3000)
        # Try to click consent/cookie button
        try:
            btn = pg2.locator("button:has-text('Принять'), button:has-text('OK'), [class*='consent'] button, [class*='cookie'] button").first
            if btn.is_visible(timeout=2000):
                btn.click()
                pg2.wait_for_timeout(2000)
        except:
            pass
        pg2.wait_for_timeout(5000)
        html_av3 = pg2.content()
        offers3 = re.findall(r'data-autoprefixer="(\d+)"', html_av3)
        prices3 = re.findall(r'(\d[\d\s\u00a0]{2,10})\s*₽', html_av3)
        links3 = re.findall(r'href="/vladivostok/[^"]+/\d+"', html_av3)
        # Check for "listing" items
        items3 = re.findall(r'class="[^"]*(?:listing|item|offer|card)[^"]*"', html_av3, re.I)
        br.close()
        mark("AV:pw_consent", len(offers3)>0 or len(prices3)>3 or len(links3)>5,
             f"len={len(html_av3)} offers={len(offers3)} prices={len(prices3)} links={len(links3)} items={len(items3)}")
except Exception as e:
    mark("AV:pw_consent", False, str(e)[:100])

# AV-C: RSS with different paths
try:
    if CFFI:
        rss_paths = [
            "https://www.avito.ru/rss/vladivostok/transport/avtomobili/",
            "https://www.avito.ru/rss/vladivostok/transport/avtomobili",
            "https://www.avito.ru/vladivostok/transport/avtomobili/rss",
            "https://www.avito.ru/rss?city=vladivostok&cat=transport/avtomobili",
        ]
        for rp in rss_paths:
            r_rss = cffi_req.get(rp, impersonate="chrome", timeout=10,
                                headers={"Accept":"application/rss+xml,application/xml","Accept-Language":"ru-RU"})
            items = len(re.findall(r'<item>', r_rss.text))
            if r_rss.status_code == 200 and items > 0:
                mark("AV:rss_paths", True, f"path={rp} items={items}")
                break
            if r_rss.status_code == 200:
                mark("AV:rss_paths", False, f"path={rp} 200 but items=0 len={len(r_rss.content)}")
        else:
            mark("AV:rss_paths", False, f"last={r_rss.status_code} all {len(rss_paths)} paths tried")
except Exception as e:
    mark("AV:rss_paths", False, str(e)[:80])

# ============================================================
# SAVE
# ============================================================
results["_meta"] = {"total_s": round(time.monotonic()-t0,1),
                    "time": datetime.now(timezone(timedelta(hours=10))).isoformat()}
with open("d28_results.json","w",encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
log(f"DONE {time.monotonic()-t0:.1f}s")
