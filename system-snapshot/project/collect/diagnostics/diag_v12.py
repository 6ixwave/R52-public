"""V12: Fast 4×4 probe. Zero-padded WB hosts. Target <60s."""
import json, os, sys, time, logging, re, traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
TZ = timezone(timedelta(hours=10))
NOW = lambda: datetime.now(TZ).isoformat()

LOGF = BASE / "diag_v12_out.txt"
hf = logging.FileHandler(LOGF, mode="w", encoding="utf-8")
hc = logging.StreamHandler(sys.stdout)
fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
hf.setFormatter(fmt); hc.setFormatter(fmt)
log = logging.getLogger("V12")
log.setLevel(logging.INFO)
log.addHandler(hf); log.addHandler(hc)

R = {}
t0 = time.monotonic()
log.info("=== V12 START ===")

from curl_cffi import requests as cffi

# ═══════════════════════════════════════════
# WB: 4 strategies (zero-padded hosts!)
# ═══════════════════════════════════════════
log.info("--- WB ---")
nm = 1097975275
vol, part = nm // 100000, nm // 1000
cpath = f"/vol{vol}/part{part}/{nm}/info/ru/card.json"
H_json = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}
wb = {}

# W1: basket-01 (zero-padded!)
for host in ("basket-01.wbbasket.ru", "basket-10.wbbasket.ru", "static-basket-01.wbbasket.ru"):
    try:
        t1 = time.monotonic()
        r = cffi.get(f"https://{host}{cpath}", impersonate="chrome", timeout=6, headers=H_json)
        el = time.monotonic() - t1
        key = host.split(".")[0]
        wb[key] = {"http": r.status_code, "len": len(r.content), "t": round(el,1)}
        if r.status_code == 200:
            d = r.json()
            wb[key].update({"title": d.get("imt_name","")[:60],
                            "brand": d.get("selling",{}).get("brand_name",""),
                            "opts": len(d.get("options",[])),
                            "imgs": len(d.get("images",[]))})
            log.info("WB %s: OK '%s'", key, d.get("imt_name","")[:40])
            break
        else:
            log.info("WB %s: HTTP %d", key, r.status_code)
    except Exception as e:
        wb[host.split(".")[0]] = {"err": str(e)[:60], "t": round(time.monotonic()-t1,1)}
        log.info("WB %s: %s", host.split(".")[0], str(e)[:50])

# W2: card.wb.ru price v2
try:
    t1 = time.monotonic()
    r = cffi.get(f"https://card.wb.ru/cards/v2/detail?nm={nm}", impersonate="chrome", timeout=8,
                 headers={"Accept": "application/json, text/plain, */*",
                          "Referer": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
                          "Origin": "https://www.wildberries.ru",
                          "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
                          "Sec-Fetch-Site": "same-site"})
    el = time.monotonic() - t1
    wb["price_v2"] = {"http": r.status_code, "len": len(r.content), "t": round(el,1)}
    if r.status_code == 200:
        items = r.json().get("data",{}).get("items",[])
        if items:
            p = items[0].get("price",{})
            wb["price_v2"].update({"basic": p.get("basic"), "product": p.get("product"),
                                   "sale": p.get("sale"), "total": p.get("total"),
                                   "sizes": len(items[0].get("sizes",[]))})
    log.info("WB price_v2: %s", wb["price_v2"])
except Exception as e:
    wb["price_v2"] = {"err": str(e)[:60]}

# W3: card.wb.ru price v1 POST
try:
    t1 = time.monotonic()
    r = cffi.post("https://card.wb.ru/cards/v1/detail",
                  json={"nm": [nm], "curr": "rub", "ext": "info,im,marketplace"},
                  impersonate="chrome", timeout=8,
                  headers={"Content-Type": "application/json", "Accept": "application/json",
                           "Referer": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
                           "Origin": "https://www.wildberries.ru"})
    el = time.monotonic() - t1
    wb["price_v1"] = {"http": r.status_code, "len": len(r.content), "t": round(el,1)}
    if r.status_code == 200:
        items = r.json().get("data",{}).get("items",[])
        if items:
            p = items[0].get("price",{})
            wb["price_v1"].update({"basic": p.get("basic"), "product": p.get("product"),
                                   "sale": p.get("sale"), "total": p.get("total")})
    log.info("WB price_v1: %s", wb["price_v1"])
except Exception as e:
    wb["price_v1"] = {"err": str(e)[:60]}

# W4: HTML detail.aspx (session)
try:
    sess = cffi.Session(impersonate="chrome")
    sess.get("https://www.wildberries.ru/", timeout=8)
    time.sleep(0.5)
    t1 = time.monotonic()
    r = sess.get(f"https://www.wildberries.ru/catalog/{nm}/detail.aspx", timeout=10,
                 headers={"Accept": "text/html", "Accept-Language": "ru-RU",
                          "sec-ch-ua": '"Google Chrome";v="131","Chromium";v="131","Not_A Brand";v="24"',
                          "sec-ch-ua-platform": '"Windows"',
                          "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                          "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
                          "Upgrade-Insecure-Requests": "1"})
    el = time.monotonic() - t1
    html = r.text
    wb["html"] = {"http": r.status_code, "len": len(html), "t": round(el,1)}
    if r.status_code == 200 and len(html) > 5000:
        og_t = re.search(r'property="og:title"\s+content="([^"]*)"', html)
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        wb["html"].update({"og_title": og_t.group(1)[:50] if og_t else None,
                           "og_price": og_p.group(1) if og_p else None})
        nd = re.search(r'id="__NEXT_DATA__"', html)
        wb["html"]["next_data"] = bool(nd)
    elif r.status_code == 498:
        wb["html"]["note"] = "498 = WB anti-bot challenge"
    log.info("WB html: %s", wb["html"])
except Exception as e:
    wb["html"] = {"err": str(e)[:60]}

R["wb"] = wb

# ═══════════════════════════════════════════
# FARPOST: 3 strategies
# ═══════════════════════════════════════════
log.info("--- FARPOST ---")
fp = {}
sess_fp = cffi.Session(impersonate="chrome")

# F1: warm + card
try:
    t1 = time.monotonic()
    w = sess_fp.get("https://www.farpost.ru/", timeout=8)
    fp["warm"] = {"http": w.status_code, "cookies": list(sess_fp.cookies.keys()), "t": round(time.monotonic()-t1,1)}
    time.sleep(1.5)
    t1 = time.monotonic()
    r = sess_fp.get("https://www.farpost.ru/vladivostok/139122082.html", timeout=12, allow_redirects=True)
    html = r.text
    fu = str(r.url)
    fp["card"] = {"http": r.status_code, "len": len(html), "url": fu[:80],
                  "verify": "/verify" in fu, "t": round(time.monotonic()-t1,1)}
    log.info("FP card: %s", fp["card"])
    
    if "/verify" in fu:
        # Save verify page
        (BASE / "diag_v12_fp_verify.html").write_text(html, encoding="utf-8")
        # Analyze verify
        vp = {}
        vp["title"] = (re.search(r'<title[^>]*>([^<]+)</title>', html, re.I) or [None,""])[1].strip()[:80] if re.search(r'<title[^>]*>([^<]+)</title>', html, re.I) else None
        vp["meta_refresh"] = re.findall(r'content=["\']([^"\']*)["\'][^>]*http-equiv=["\']refresh', html, re.I) + \
                             re.findall(r'http-equiv=["\']refresh["\'][^>]*content=["\']([^"\']*)["\']', html, re.I)
        vp["forms"] = re.findall(r'<form[^>]*action=["\']([^"\']*)["\']', html, re.I)[:3]
        vp["iframes"] = re.findall(r'<iframe[^>]*src=["\']([^"\']*)["\']', html, re.I)[:3]
        vp["hidden"] = re.findall(r'type=["\']hidden["\'][^>]*name=["\']([^"\']*)["\']', html, re.I)[:8]
        vp["n_scripts"] = len(re.findall(r'<script', html, re.I))
        # Look for JS challenge patterns
        vp["has_ring"] = "ring" in html.lower()
        vp["has_captcha"] = "captcha" in html.lower()
        vp["has_js_challenge"] = bool(re.search(r'(?:challenge|verify|nonce|token|check)', html, re.I))
        # First 3 script bodies (trimmed)
        scs = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        vp["scripts"] = [s.strip()[:150] for s in scs[:4] if s.strip()]
        fp["verify_analysis"] = vp
        log.info("FP verify: %s", json.dumps(vp, ensure_ascii=False, default=str)[:400])
        
        # F1b: retry after verify
        time.sleep(2)
        t1 = time.monotonic()
        r2 = sess_fp.get("https://www.farpost.ru/vladivostok/139122082.html", timeout=12)
        fp["card_retry"] = {"http": r2.status_code, "len": len(r2.text),
                            "verify": "/verify" in str(r2.url), "t": round(time.monotonic()-t1,1),
                            "cookies": list(sess_fp.cookies.keys())}
        log.info("FP retry: %s", fp["card_retry"])
    
    # Parse card if not verify
    target_html = html
    if fp["card"].get("verify") and "card_retry" in fp and not fp["card_retry"]["verify"]:
        target_html = r2.text
    if r.status_code == 200 and len(target_html) > 5000:
        lds = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', target_html, re.DOTALL)
        fp["n_ld"] = len(lds)
        for ld in lds[:3]:
            try:
                o = json.loads(ld.strip())
                if o.get("@type") == "Product":
                    fp["ld_title"] = o.get("name","")[:50]
                    fp["ld_price"] = o.get("offers",{}).get("price")
            except: pass
        pm = re.search(r'data-bulletin-price=["\'](\d+)["\']', target_html)
        fp["attr_price"] = int(pm.group(1)) if pm else None
        photos = re.findall(r'"(https?://static\.baza\.farpost\.ru/[^"]+)"', target_html)
        fp["n_photos"] = len([p for p in photos if not any(s in p for s in ('.svg','.gif','.ico','_thumbnail'))])
        log.info("FP parsed: ld=%s price=%s photos=%d", fp.get("n_ld"), fp.get("attr_price"), fp.get("n_photos",0))
except Exception as e:
    fp["card"] = {"err": str(e)[:100]}
    log.info("FP err: %s", e)

# F2: Mobile
try:
    t1 = time.monotonic()
    r = sess_fp.get("https://m.farpost.ru/vladivostok/139122082.html", timeout=10)
    fp["mobile"] = {"http": r.status_code, "len": len(r.text),
                    "verify": "/verify" in str(r.url), "t": round(time.monotonic()-t1,1)}
    log.info("FP mobile: %s", fp["mobile"])
except Exception as e:
    fp["mobile"] = {"err": str(e)[:60]}

# F3: Category page
try:
    t1 = time.monotonic()
    r = sess_fp.get("https://www.farpost.ru/vladivostok/electronics", timeout=10)
    html = r.text
    fp["cat"] = {"http": r.status_code, "len": len(html),
                 "verify": "/verify" in str(r.url), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200 and len(html) > 5000 and "/verify" not in str(r.url):
        prices = re.findall(r'data-bulletin-price=["\'](\d+)["\']', html)
        fp["cat"]["n_prices"] = len(prices)
        fp["cat"]["first_prices"] = prices[:5]
    log.info("FP cat: %s", fp["cat"])
except Exception as e:
    fp["cat"] = {"err": str(e)[:60]}

R["farpost"] = fp

# ═══════════════════════════════════════════
# OZON: 4 strategies (session-based)
# ═══════════════════════════════════════════
log.info("--- OZON ---")
oz = {}
sess_oz = cffi.Session(impersonate="chrome")
lid = "3481320800"

# O1: Homepage (get cookies)
try:
    t1 = time.monotonic()
    r0 = sess_oz.get("https://www.ozon.ru/", timeout=10)
    oz["home"] = {"http": r0.status_code, "len": len(r0.text),
                  "cookies": list(sess_oz.cookies.keys()), "t": round(time.monotonic()-t1,1)}
    log.info("OZ home: %s", oz["home"])
except Exception as e:
    oz["home"] = {"err": str(e)[:60]}

# O2: Product SSR (same session)
try:
    time.sleep(1)
    t1 = time.monotonic()
    r1 = sess_oz.get(f"https://www.ozon.ru/product/{lid}/", timeout=12,
                     headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                              "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                              "sec-ch-ua": '"Google Chrome";v="131","Chromium";v="131","Not_A Brand";v="24"',
                              "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                              "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                              "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
                              "Upgrade-Insecure-Requests": "1"})
    html = r1.text
    oz["ssr"] = {"http": r1.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r1.status_code == 200 and len(html) > 5000:
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        og_t = re.search(r'property="og:title"\s+content="([^"]*)"', html)
        oz["ssr"].update({"og_price": og_p.group(1) if og_p else None,
                          "og_title": og_t.group(1)[:50] if og_t else None})
        lds = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        oz["ssr"]["n_ld"] = len(lds)
        dw = list(set(re.findall(r'data-widget="([^"]+)"', html)))[:8]
        oz["ssr"]["widgets"] = dw
    elif r1.status_code == 403:
        oz["ssr"]["body_200"] = html[:200]
    log.info("OZ ssr: %s", oz["ssr"])
except Exception as e:
    oz["ssr"] = {"err": str(e)[:60]}

# O3: Composer API (same session)
try:
    t1 = time.monotonic()
    r2 = sess_oz.get(f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{lid}/",
                     timeout=10, verify=False,
                     headers={"Accept": "application/json", "Referer": "https://www.ozon.ru/"})
    oz["composer"] = {"http": r2.status_code, "len": len(r2.content), "t": round(time.monotonic()-t1,1)}
    if r2.status_code == 200:
        d = r2.json()
        oz["composer"]["keys"] = list(d.keys())[:8] if isinstance(d, dict) else []
    log.info("OZ composer: %s", oz["composer"])
except Exception as e:
    oz["composer"] = {"err": str(e)[:60]}

# O4: entrypoint API
try:
    t1 = time.monotonic()
    r3 = sess_oz.get(f"https://www.ozon.ru/api/entrypoint/pdp/{lid}", timeout=10,
                     headers={"Accept": "application/json",
                              "Referer": f"https://www.ozon.ru/product/{lid}/"})
    oz["entrypoint"] = {"http": r3.status_code, "len": len(r3.content), "t": round(time.monotonic()-t1,1)}
    if r3.status_code == 200:
        d = r3.json()
        oz["entrypoint"]["keys"] = list(d.keys())[:10] if isinstance(d, dict) else []
    log.info("OZ entrypoint: %s", oz["entrypoint"])
except Exception as e:
    oz["entrypoint"] = {"err": str(e)[:60]}

R["ozon"] = oz

# ═══════════════════════════════════════════
# AVITO: 3 strategies (session-based)
# ═══════════════════════════════════════════
log.info("--- AVITO ---")
av = {}
sess_av = cffi.Session(impersonate="chrome")

# A1: Homepage
try:
    t1 = time.monotonic()
    r0 = sess_av.get("https://www.avito.ru/", timeout=10)
    av["home"] = {"http": r0.status_code, "len": len(r0.text),
                  "cookies": list(sess_av.cookies.keys()), "t": round(time.monotonic()-t1,1)}
    log.info("AV home: %s", av["home"])
except Exception as e:
    av["home"] = {"err": str(e)[:60]}

# A2: Search (same session)
try:
    time.sleep(1)
    t1 = time.monotonic()
    r1 = sess_av.get("https://www.avito.ru/vladivostok/smartfony", timeout=12,
                     headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                              "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                              "sec-ch-ua": '"Google Chrome";v="131","Chromium";v="131","Not_A Brand";v="24"',
                              "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                              "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                              "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
                              "Upgrade-Insecure-Requests": "1"})
    html = r1.text
    av["search"] = {"http": r1.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r1.status_code == 200:
        prices = re.findall(r'data-price="(\d+)"', html)
        av["search"]["n_prices"] = len(prices)
        av["search"]["first_prices"] = prices[:5]
        titles = re.findall(r'data-qa="index-item-title"[^>]*>([^<]+)<', html)
        av["search"]["n_titles"] = len(titles)
    elif r1.status_code in (403, 429, 439):
        av["search"]["blocked"] = True
        (BASE / "diag_v12_av_439.html").write_text(html, encoding="utf-8")
        av["search"]["captcha"] = "captcha" in html.lower()
        av["search"]["antibot"] = "antibot" in html.lower()
        sc = re.findall(r'<script[^>]*src=["\']([^"\']*)["\']', html, re.I)
        av["search"]["scripts"] = sc[:5]
    log.info("AV search: %s", av["search"])
except Exception as e:
    av["search"] = {"err": str(e)[:60]}

# A3: Card
try:
    t1 = time.monotonic()
    r2 = sess_av.get("https://www.avito.ru/ad/4097975275", timeout=10,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU"})
    html = r2.text
    av["card"] = {"http": r2.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r2.status_code == 200 and len(html) > 3000:
        lds = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        av["card"]["n_ld"] = len(lds)
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        av["card"]["og_price"] = og_p.group(1) if og_p else None
    log.info("AV card: %s", av["card"])
except Exception as e:
    av["card"] = {"err": str(e)[:60]}

R["avito"] = av

# ═══════════════════════════════════════════
# Finalize
# ═══════════════════════════════════════════
R["total_s"] = round(time.monotonic() - t0, 1)
R["time"] = NOW()
out = BASE / "diag_v12_results.json"
out.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
log.info("=== V12 DONE t=%.1fs ===", R["total_s"])
log.info("→ %s", out)
