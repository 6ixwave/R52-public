"""V10: Multi-strategy probe per marketplace. Target <70s.
Writes diag_v10_out.txt + diag_v10_results.json.
"""
import json, os, sys, time, logging, re, traceback, socket
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
TZ = timezone(timedelta(hours=10))
NOW = lambda: datetime.now(TZ).isoformat()

LOGF = BASE / "diag_v10_out.txt"
hf = logging.FileHandler(LOGF, mode="w", encoding="utf-8")
hc = logging.StreamHandler(sys.stdout)
fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
hf.setFormatter(fmt); hc.setFormatter(fmt)
log = logging.getLogger("V10")
log.setLevel(logging.INFO)
log.addHandler(hf); log.addHandler(hc)

R = {}
t0 = time.monotonic()
log.info("=== V10 START ===")

from curl_cffi import requests as cffi

# ═══════════════════════════════════════════
# WB: 4 strategies
# ═══════════════════════════════════════════
log.info("--- WB ---")
nm = 1097975275
vol, part = nm // 100000, nm // 1000
cpath = f"/vol{vol}/part{part}/{nm}/info/ru/card.json"
wb = {}

# W1: static-basket (different naming pattern)
for i in range(10):
    host = f"static-basket-{i}.wbbasket.ru"
    try:
        t1 = time.monotonic()
        r = cffi.get(f"https://{host}{cpath}", impersonate="chrome", timeout=5,
                     headers={"Accept": "application/json", "Referer": "https://www.wildberries.ru/"})
        if r.status_code == 200:
            d = r.json()
            wb[f"static_{i}"] = {"ok": True, "title": d.get("imt_name","")[:50],
                                  "brand": d.get("selling",{}).get("brand_name",""),
                                  "opts": len(d.get("options",[])), "t": round(time.monotonic()-t1,1)}
            log.info("WB static-%d: OK %s", i, d.get("imt_name","")[:40])
            break
    except Exception as e:
        if i < 3:
            log.info("WB static-%d: %s", i, str(e)[:60])

# W2: HTML detail.aspx (parsing JSON from __NEXT_DATA__ or inline)
try:
    t1 = time.monotonic()
    r = cffi.get(f"https://www.wildberries.ru/catalog/{nm}/detail.aspx", impersonate="chrome", timeout=12,
                 headers={"Accept": "text/html", "Accept-Language": "ru-RU",
                          "Referer": "https://www.wildberries.ru/",
                          "sec-ch-ua": '"Google Chrome";v="131","Chromium";v="131","Not_A Brand";v="24"',
                          "sec-ch-ua-platform": '"Windows"',
                          "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                          "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
                          "Upgrade-Insecure-Requests": "1"})
    html = r.text
    wb["html"] = {"http": r.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200 and len(html) > 5000:
        # Try __NEXT_DATA__
        nm_m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if nm_m:
            try:
                nd = json.loads(nm_m.group(1))
                wb["html"]["next_data"] = True
                wb["html"]["nd_keys"] = list(nd.keys())[:5]
            except: pass
        # og tags
        og_t = re.search(r'property="og:title"\s+content="([^"]*)"', html)
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        wb["html"]["og_title"] = og_t.group(1)[:50] if og_t else None
        wb["html"]["og_price"] = og_p.group(1) if og_p else None
        # Inline JSON
        ij = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
        wb["html"]["has_state"] = bool(ij)
    log.info("WB html: %s", wb["html"])
except Exception as e:
    wb["html"] = {"err": str(e)[:80]}
    log.info("WB html: %s", e)

# W3: Price API v2 with full headers + Origin
try:
    t1 = time.monotonic()
    r = cffi.get(f"https://card.wb.ru/cards/v2/detail?nm={nm}", impersonate="chrome", timeout=10,
                 headers={"Accept": "application/json, text/plain, */*",
                          "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                          "Referer": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
                          "Origin": "https://www.wildberries.ru",
                          "sec-ch-ua": '"Google Chrome";v="131","Chromium";v="131","Not_A Brand";v="24"',
                          "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                          "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
                          "Sec-Fetch-Site": "same-site"})
    wb["price_v2"] = {"http": r.status_code, "len": len(r.content), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200:
        items = r.json().get("data",{}).get("items",[])
        if items:
            p = items[0].get("price",{})
            wb["price_v2"].update({"basic": p.get("basic"), "product": p.get("product"),
                                   "sale": p.get("sale"), "total": p.get("total"),
                                   "sizes": len(items[0].get("sizes",[]))})
    log.info("WB price_v2: %s", wb["price_v2"])
except Exception as e:
    wb["price_v2"] = {"err": str(e)[:80]}

# W4: Price API v1 POST
try:
    t1 = time.monotonic()
    r = cffi.post("https://card.wb.ru/cards/v1/detail",
                  json={"nm": [nm], "curr": "rub", "ext": "info,im,marketplace"},
                  impersonate="chrome", timeout=10,
                  headers={"Content-Type": "application/json",
                           "Accept": "application/json",
                           "Referer": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
                           "Origin": "https://www.wildberries.ru"})
    wb["price_v1"] = {"http": r.status_code, "len": len(r.content), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200:
        items = r.json().get("data",{}).get("items",[])
        if items:
            p = items[0].get("price",{})
            wb["price_v1"].update({"basic": p.get("basic"), "product": p.get("product"),
                                   "sale": p.get("sale"), "total": p.get("total")})
    log.info("WB price_v1: %s", wb["price_v1"])
except Exception as e:
    wb["price_v1"] = {"err": str(e)[:80]}

R["wb"] = wb

# ═══════════════════════════════════════════
# FARPOST: 3 strategies
# ═══════════════════════════════════════════
log.info("--- FARPOST ---")
fp = {}
sess = cffi.Session(impersonate="chrome")

# F1: Warm-up + card with /verify follow
try:
    t1 = time.monotonic()
    w = sess.get("https://www.farpost.ru/", timeout=10)
    fp["warm"] = {"http": w.status_code, "cookies": list(sess.cookies.keys()), "t": round(time.monotonic()-t1,1)}
    log.info("FP warm: %s", fp["warm"])
    time.sleep(2)

    t1 = time.monotonic()
    r = sess.get("https://www.farpost.ru/vladivostok/139122082.html", timeout=15, allow_redirects=True)
    html = r.text
    fu = str(r.url)
    fp["card"] = {"http": r.status_code, "len": len(html), "url": fu[:80],
                  "verify": "/verify" in fu, "t": round(time.monotonic()-t1,1),
                  "cookies_after": list(sess.cookies.keys())}
    if "/verify" in fu:
        # F1b: Follow verify - GET the verify URL, it should set cookie + redirect
        log.info("FP /verify detected, following...")
        time.sleep(3)
        t2 = time.monotonic()
        r2 = sess.get("https://www.farpost.ru/vladivostok/139122082.html", timeout=15, allow_redirects=True)
        html2 = r2.text
        fu2 = str(r2.url)
        fp["card_retry"] = {"http": r2.status_code, "len": len(html2), "url": fu2[:80],
                            "verify": "/verify" in fu2, "t": round(time.monotonic()-t2,1),
                            "cookies_after": list(sess.cookies.keys())}
        if not fp["card_retry"]["verify"] and r2.status_code == 200:
            html = html2
    if r.status_code == 200 and len(html) > 5000 and "/verify" not in str(r.url):
        lds = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        fp["card"]["n_ld"] = len(lds)
        for ld in lds[:3]:
            try:
                o = json.loads(ld.strip())
                if o.get("@type") == "Product":
                    fp["card"]["ld_title"] = o.get("name","")[:50]
                    fp["card"]["ld_price"] = o.get("offers",{}).get("price")
            except: pass
        pm = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        fp["card"]["attr_price"] = int(pm.group(1)) if pm else None
        photos = re.findall(r'"(https?://static\.baza\.farpost\.ru/[^"]+)"', html)
        fp["card"]["n_photos"] = len([p for p in photos if not any(s in p for s in ('.svg','.gif','.ico','_thumbnail'))])
    log.info("FP card: %s", {k:v for k,v in fp["card"].items() if k != "cookies_after"})
except Exception as e:
    fp["card"] = {"err": str(e)[:100]}
    log.info("FP card err: %s", e)

# F2: Mobile farpost
try:
    t1 = time.monotonic()
    r = sess.get("https://m.farpost.ru/vladivostok/139122082.html", timeout=12,
                 headers={"Accept": "text/html", "Accept-Language": "ru-RU"})
    html = r.text
    fp["mobile"] = {"http": r.status_code, "len": len(html), "url": str(r.url)[:60],
                    "verify": "/verify" in str(r.url), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200 and len(html) > 3000 and "/verify" not in str(r.url):
        pm = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        fp["mobile"]["attr_price"] = int(pm.group(1)) if pm else None
        tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        fp["mobile"]["title"] = tm.group(1).strip()[:50] if tm else None
    log.info("FP mobile: %s", fp["mobile"])
except Exception as e:
    fp["mobile"] = {"err": str(e)[:80]}

# F3: Category page (listings, not single card)
try:
    t1 = time.monotonic()
    r = sess.get("https://www.farpost.ru/vladivostok/electronics", timeout=12)
    html = r.text
    fp["category"] = {"http": r.status_code, "len": len(html), "url": str(r.url)[:60],
                      "verify": "/verify" in str(r.url), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200 and len(html) > 5000:
        # Count bulletin links
        blinks = re.findall(r'href="(/[^"]*-\d+\.html)"', html)
        fp["category"]["n_links"] = len(blinks)
        fp["category"]["first_links"] = blinks[:3]
        # Prices in listing
        prices = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html)
        fp["category"]["n_prices"] = len(prices)
        fp["category"]["first_prices"] = prices[:5]
    log.info("FP category: %s", fp["category"])
except Exception as e:
    fp["category"] = {"err": str(e)[:80]}

R["farpost"] = fp

# ═══════════════════════════════════════════
# OZON: 4 strategies
# ═══════════════════════════════════════════
log.info("--- OZON ---")
oz = {}
lid = "3481320800"

# O1: Composer API (fix SSL with verify=False)
try:
    t1 = time.monotonic()
    r = cffi.get(f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{lid}/",
                 impersonate="chrome", timeout=12, verify=False,
                 headers={"Accept": "application/json", "Referer": "https://www.ozon.ru/",
                          "Accept-Language": "ru-RU,ru;q=0.9"})
    oz["composer"] = {"http": r.status_code, "len": len(r.content), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200:
        d = r.json()
        oz["composer"]["keys"] = list(d.keys())[:8] if isinstance(d, dict) else str(type(d))
        # Dig for title
        def dig(obj, *ks):
            for k in ks:
                if isinstance(obj, dict): obj = obj.get(k)
                else: return None
            return obj
        for paths in [("page","title"),("product","title"),("data","title"),("props","pageProps","product","title")]:
            t = dig(d, *paths)
            if t and isinstance(t, str) and len(t) > 3:
                oz["composer"]["title"] = t[:60]; break
    log.info("OZON composer: %s", oz["composer"])
except Exception as e:
    oz["composer"] = {"err": str(e)[:100]}
    log.info("OZON composer err: %s", e)

# O2: SSR with full browser headers
try:
    t1 = time.monotonic()
    r = cffi.get(f"https://www.ozon.ru/product/{lid}/", impersonate="chrome", timeout=15,
                 headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                          "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                          "Referer": "https://www.ozon.ru/",
                          "sec-ch-ua": '"Google Chrome";v="131","Chromium";v="131","Not_A Brand";v="24"',
                          "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                          "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                          "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
                          "Upgrade-Insecure-Requests": "1"})
    html = r.text
    oz["ssr"] = {"http": r.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200 and len(html) > 5000:
        lds = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        oz["ssr"]["n_ld"] = len(lds)
        for ld in lds[:5]:
            try:
                o = json.loads(ld.strip())
                if isinstance(o, dict) and o.get("@type") == "Product":
                    oz["ssr"]["ld_title"] = o.get("name","")[:50]
                    oz["ssr"]["ld_price"] = o.get("offers",{}).get("price")
                    break
                if isinstance(o, list):
                    for i in o:
                        if isinstance(i, dict) and i.get("@type") == "Product":
                            oz["ssr"]["ld_title"] = i.get("name","")[:50]
                            oz["ssr"]["ld_price"] = i.get("offers",{}).get("price")
                            break
                    if oz["ssr"].get("ld_title"): break
            except: pass
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        oz["ssr"]["og_price"] = og_p.group(1) if og_p else None
        tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        oz["ssr"]["title"] = tm.group(1).strip()[:60] if tm else None
        # data-widget
        dw = re.findall(r'data-widget="([^"]+)"', html)
        oz["ssr"]["widgets"] = list(set(dw))[:10]
    log.info("OZON ssr: %s", oz["ssr"])
except Exception as e:
    oz["ssr"] = {"err": str(e)[:80]}

# O3: Mobile Ozon
try:
    t1 = time.monotonic()
    r = cffi.get(f"https://m.ozon.ru/product/{lid}/", impersonate="chrome", timeout=12,
                 headers={"Accept": "text/html", "Accept-Language": "ru-RU",
                          "sec-ch-ua-mobile": "?1"})
    html = r.text
    oz["mobile"] = {"http": r.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200 and len(html) > 3000:
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        oz["mobile"]["og_price"] = og_p.group(1) if og_p else None
        tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        oz["mobile"]["title"] = tm.group(1).strip()[:50] if tm else None
    log.info("OZON mobile: %s", oz["mobile"])
except Exception as e:
    oz["mobile"] = {"err": str(e)[:80]}

# O4: entrypoint API
try:
    t1 = time.monotonic()
    r = cffi.get(f"https://www.ozon.ru/api/entrypoint/pdp/{lid}", impersonate="chrome", timeout=12,
                 headers={"Accept": "application/json", "Referer": f"https://www.ozon.ru/product/{lid}/",
                          "Accept-Language": "ru-RU"})
    oz["entrypoint"] = {"http": r.status_code, "len": len(r.content), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200:
        d = r.json()
        oz["entrypoint"]["keys"] = list(d.keys())[:10] if isinstance(d, dict) else []
    log.info("OZON entrypoint: %s", oz["entrypoint"])
except Exception as e:
    oz["entrypoint"] = {"err": str(e)[:80]}

R["ozon"] = oz

# ═══════════════════════════════════════════
# AVITO: 4 strategies
# ═══════════════════════════════════════════
log.info("--- AVITO ---")
av = {}
av_sess = cffi.Session(impersonate="chrome")

# A1: Desktop search with full headers
try:
    t1 = time.monotonic()
    r = av_sess.get("https://www.avito.ru/vladivostok/smartfony", impersonate="chrome", timeout=15,
                    headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                             "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                             "sec-ch-ua": '"Google Chrome";v="131","Chromium";v="131","Not_A Brand";v="24"',
                             "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                             "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                             "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
                             "Upgrade-Insecure-Requests": "1"})
    html = r.text
    av["search"] = {"http": r.status_code, "len": len(html), "t": round(time.monotonic()-t1,1),
                    "cookies": list(av_sess.cookies.keys())}
    if r.status_code == 200:
        av["search"]["captcha"] = "captcha" in html.lower() or "antibot" in html.lower()
        prices = re.findall(r'data-price="(\d+)"', html)
        av["search"]["n_prices"] = len(prices)
        av["search"]["first_prices"] = prices[:5]
        titles = re.findall(r'data-qa="index-item-title"[^>]*>([^<]+)<', html)
        av["search"]["n_titles"] = len(titles)
        av["search"]["first_titles"] = [t.strip()[:40] for t in titles[:3]]
    elif r.status_code in (403, 429, 439):
        av["search"]["blocked"] = True
        # Check for cookie-based challenge
        cm = re.search(r'set-cookie[^;]*', str(r.headers), re.I)
        av["search"]["cookie_hint"] = cm.group(0)[:80] if cm else None
    log.info("AV search: %s", {k:v for k,v in av["search"].items() if k != "cookies"})
except Exception as e:
    av["search"] = {"err": str(e)[:80]}

# A2: Mobile Avito
try:
    t1 = time.monotonic()
    r = av_sess.get("https://m.avito.ru/vladivostok/smartfony", impersonate="chrome", timeout=12,
                    headers={"Accept": "text/html", "Accept-Language": "ru-RU",
                             "sec-ch-ua-mobile": "?1",
                             "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"})
    html = r.text
    av["mobile"] = {"http": r.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200:
        prices = re.findall(r'data-price="(\d+)"', html)
        av["mobile"]["n_prices"] = len(prices)
        av["mobile"]["first_prices"] = prices[:5]
        tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        av["mobile"]["title"] = tm.group(1).strip()[:50] if tm else None
    log.info("AV mobile: %s", av["mobile"])
except Exception as e:
    av["mobile"] = {"err": str(e)[:80]}

# A3: Avito API (internal)
try:
    t1 = time.monotonic()
    r = av_sess.get("https://www.avito.ru/api/search/listings?query=smartfony&city=vladivostok&limit=20",
                    impersonate="chrome", timeout=12,
                    headers={"Accept": "application/json", "Accept-Language": "ru-RU",
                             "Referer": "https://www.avito.ru/vladivostok/smartfony"})
    av["api"] = {"http": r.status_code, "len": len(r.content), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200:
        d = r.json()
        av["api"]["keys"] = list(d.keys())[:8] if isinstance(d, dict) else []
        items = d.get("items", d.get("listings", []))
        av["api"]["n_items"] = len(items) if isinstance(items, list) else 0
    log.info("AV api: %s", av["api"])
except Exception as e:
    av["api"] = {"err": str(e)[:80]}

# A4: Avito card (single listing)
try:
    # Use a known listing ID from earlier tests
    card_id = "4097975275"
    t1 = time.monotonic()
    r = av_sess.get(f"https://www.avito.ru/ad/{card_id}", impersonate="chrome", timeout=12,
                    headers={"Accept": "text/html", "Accept-Language": "ru-RU"})
    html = r.text
    av["card"] = {"http": r.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200 and len(html) > 3000:
        lds = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        av["card"]["n_ld"] = len(lds)
        for ld in lds[:3]:
            try:
                o = json.loads(ld.strip())
                if o.get("@type") == "Product":
                    av["card"]["ld_title"] = o.get("name","")[:50]
                    av["card"]["ld_price"] = o.get("offers",{}).get("price")
            except: pass
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        av["card"]["og_price"] = og_p.group(1) if og_p else None
    log.info("AV card: %s", av["card"])
except Exception as e:
    av["card"] = {"err": str(e)[:80]}

R["avito"] = av

# ═══════════════════════════════════════════
# Finalize
# ═══════════════════════════════════════════
R["total_s"] = round(time.monotonic() - t0, 1)
R["time"] = NOW()
out = BASE / "diag_v10_results.json"
out.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
log.info("=== V10 DONE t=%.1fs ===", R["total_s"])
log.info("→ %s", out)
