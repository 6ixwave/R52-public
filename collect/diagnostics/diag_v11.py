"""V11: Deep probe — DNS, /verify analysis, session strategies. Target <75s."""
import json, os, sys, time, logging, re, traceback, socket
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
TZ = timezone(timedelta(hours=10))
NOW = lambda: datetime.now(TZ).isoformat()

LOGF = BASE / "diag_v11_out.txt"
hf = logging.FileHandler(LOGF, mode="w", encoding="utf-8")
hc = logging.StreamHandler(sys.stdout)
fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
hf.setFormatter(fmt); hc.setFormatter(fmt)
log = logging.getLogger("V11")
log.setLevel(logging.INFO)
log.addHandler(hf); log.addHandler(hc)

R = {}
t0 = time.monotonic()
log.info("=== V11 START ===")

from curl_cffi import requests as cffi

# ═══════════════════════════════════════════
# 1. WB DNS RESOLUTION
# ═══════════════════════════════════════════
log.info("--- WB DNS ---")
wb_dns = {}
wb_hosts = [
    "basket-1.wbbasket.ru", "basket-01.wbbasket.ru",
    "basket-2.wbbasket.ru", "basket-10.wbbasket.ru",
    "static-basket-1.wbbasket.ru", "static-basket-01.wbbasket.ru",
    "static-basket-10.wbbasket.ru",
    "img.wbbasket.ru", "card.wb.ru",
    "www.wildberries.ru", "basket.wbbasket.ru",
]
for h in wb_hosts:
    try:
        t1 = time.monotonic()
        ip = socket.getaddrinfo(h, 443, socket.AF_INET)[0][4][0]
        wb_dns[h] = {"ip": ip, "t": round(time.monotonic()-t1, 2)}
    except Exception as e:
        wb_dns[h] = {"err": str(e)[:60]}
    log.info("DNS %s: %s", h, wb_dns[h])
R["wb_dns"] = wb_dns

# WB: try working hosts
nm = 1097975275
vol, part = nm // 100000, nm // 1000
cpath = f"/vol{vol}/part{part}/{nm}/info/ru/card.json"
H = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}
wb_net = {}

# Try resolved basket hosts
for h, info in wb_dns.items():
    if "ip" in info and "basket" in h:
        try:
            t1 = time.monotonic()
            r = cffi.get(f"https://{h}{cpath}", impersonate="chrome", timeout=6, headers=H)
            wb_net[h] = {"http": r.status_code, "len": len(r.content), "t": round(time.monotonic()-t1,1)}
            if r.status_code == 200:
                d = r.json()
                wb_net[h].update({"title": d.get("imt_name","")[:50], "opts": len(d.get("options",[]))})
                log.info("WB %s: OK %s", h, d.get("imt_name","")[:40])
                break
        except Exception as e:
            wb_net[h] = {"err": str(e)[:60]}
R["wb_basket"] = wb_net
log.info("WB basket results: %s", wb_net)

# WB: session approach (visit main → catalog → API)
try:
    sess = cffi.Session(impersonate="chrome")
    t1 = time.monotonic()
    r0 = sess.get("https://www.wildberries.ru/", timeout=10)
    log.info("WB main: HTTP=%d cookies=%s t=%.1fs", r0.status_code, list(sess.cookies.keys()), time.monotonic()-t1)
    time.sleep(1)
    t1 = time.monotonic()
    r1 = sess.get(f"https://www.wildberries.ru/catalog/{nm}/detail.aspx", timeout=12,
                  headers={"Accept": "text/html", "Accept-Language": "ru-RU",
                           "sec-ch-ua": '"Google Chrome";v="131","Chromium";v="131","Not_A Brand";v="24"',
                           "sec-ch-ua-platform": '"Windows"',
                           "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                           "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
                           "Upgrade-Insecure-Requests": "1"})
    html = r1.text
    wb_net["sess_html"] = {"http": r1.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r1.status_code == 200 and len(html) > 5000:
        og_t = re.search(r'property="og:title"\s+content="([^"]*)"', html)
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        wb_net["sess_html"].update({"og_title": og_t.group(1)[:50] if og_t else None,
                                     "og_price": og_p.group(1) if og_p else None})
        # Check for __NEXT_DATA__ or state
        nd = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        wb_net["sess_html"]["next_data"] = bool(nd)
        # Inline state
        st = re.search(r'window\.__INITIAL_STATE__\s*=', html)
        wb_net["sess_html"]["has_state"] = bool(st)
    log.info("WB sess_html: %s", wb_net["sess_html"])
    # Now try API in same session
    t1 = time.monotonic()
    r2 = sess.get(f"https://card.wb.ru/cards/v2/detail?nm={nm}", timeout=10,
                  headers={"Accept": "application/json", "Referer": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"})
    wb_net["sess_price"] = {"http": r2.status_code, "len": len(r2.content), "t": round(time.monotonic()-t1,1)}
    if r2.status_code == 200:
        items = r2.json().get("data",{}).get("items",[])
        if items:
            p = items[0].get("price",{})
            wb_net["sess_price"].update({"basic": p.get("basic"), "product": p.get("product"),
                                          "sale": p.get("sale"), "total": p.get("total")})
    log.info("WB sess_price: %s", wb_net["sess_price"])
except Exception as e:
    wb_net["sess_err"] = str(e)[:100]
    log.info("WB sess err: %s", e)

# ═══════════════════════════════════════════
# 2. FARPOST /verify DEEP ANALYSIS
# ═══════════════════════════════════════════
log.info("--- FP /verify ---")
fp = {}
sess_fp = cffi.Session(impersonate="chrome")
t1 = time.monotonic()
sess_fp.get("https://www.farpost.ru/", timeout=10)
time.sleep(1)
t1 = time.monotonic()
r = sess_fp.get("https://www.farpost.ru/vladivostok/139122082.html", timeout=15)
html = r.text
fu = str(r.url)
fp["card"] = {"http": r.status_code, "len": len(html), "url": fu[:80],
              "verify": "/verify" in fu, "t": round(time.monotonic()-t1,1)}
log.info("FP card: %s", fp["card"])

# Save /verify page for analysis
verify_html_path = BASE / "diag_v11_fp_verify.html"
verify_html_path.write_text(html, encoding="utf-8")
log.info("FP verify saved: %d bytes", len(html))

# Analyze /verify page
vp = {}
# Meta refresh
mr = re.findall(r'<meta[^>]*http-equiv=["\']refresh["\'][^>]*content=["\']([^"\']+)', html, re.I)
vp["meta_refresh"] = mr[:3]
# Forms
forms = re.findall(r'<form[^>]*action=["\']([^"\']*)["\'][^>]*>', html, re.I)
vp["forms"] = forms[:5]
# Hidden inputs
hids = re.findall(r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']*)["\'][^>]*value=["\']([^"\']*)["\']', html, re.I)
vp["hidden_inputs"] = hids[:10]
# Scripts (first 500 chars each)
scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
vp["n_scripts"] = len(scripts)
vp["script_snippets"] = [s.strip()[:200] for s in scripts[:5] if s.strip()]
# data- attributes on body or main div
body_data = re.findall(r'<(?:body|div)[^>]*data-[a-z-]+=["\']([^"\']*)["\']', html[:5000], re.I)
vp["body_data"] = body_data[:10]
# iframe
iframes = re.findall(r'<iframe[^>]*src=["\']([^"\']*)["\']', html, re.I)
vp["iframes"] = iframes[:3]
# Title
tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
vp["title"] = tm.group(1).strip()[:80] if tm else None
# Look for JS that sets cookies
cookie_js = re.findall(r'(?:document\.cookie|Set-Cookie|localStorage)[^;]{0,100}', html, re.I)
vp["cookie_js"] = cookie_js[:5]
# Look for fetch/XHR calls
fetch_calls = re.findall(r'(?:fetch|XMLHttpRequest|axios)[^;]{0,150}', html, re.I)
vp["fetch_calls"] = fetch_calls[:5]
# ring cookie value
ring_val = re.search(r'ring[=;][^;]*', str(sess_fp.cookies))
vp["cookies"] = {k: str(v)[:50] for k, v in sess_fp.cookies.items()}

fp["verify_analysis"] = vp
R["farpost"] = fp
log.info("FP verify analysis: %s", json.dumps(vp, ensure_ascii=False, default=str)[:500])

# FP: try following verify (if it's a meta-refresh or form)
if vp.get("meta_refresh"):
    log.info("FP: meta-refresh found, following...")
    time.sleep(2)
    t1 = time.monotonic()
    r2 = sess_fp.get("https://www.farpost.ru/vladivostok/139122082.html", timeout=15)
    fp["after_refresh"] = {"http": r2.status_code, "len": len(r2.text),
                           "verify": "/verify" in str(r2.url), "t": round(time.monotonic()-t1,1)}
    log.info("FP after refresh: %s", fp["after_refresh"])

# FP: try with explicit cookie manipulation
try:
    t1 = time.monotonic()
    # Try: visit /verify directly, check if it sets a cookie via JS
    r3 = sess_fp.get("https://www.farpost.ru/verify?u=%2Fvladivostok%2F139122082.html", timeout=10)
    fp["verify_direct"] = {"http": r3.status_code, "len": len(r3.text), "t": round(time.monotonic()-t1,1)}
    # After verify, try card again
    time.sleep(2)
    t1 = time.monotonic()
    r4 = sess_fp.get("https://www.farpost.ru/vladivostok/139122082.html", timeout=15)
    fp["card_after_verify"] = {"http": r4.status_code, "len": len(r4.text),
                                "verify": "/verify" in str(r4.url), "t": round(time.monotonic()-t1,1)}
    if not fp["card_after_verify"]["verify"] and r4.status_code == 200:
        h4 = r4.text
        pm = re.search(r'data-bulletin-price=["\'](\d+)["\']', h4)
        fp["card_after_verify"]["price"] = int(pm.group(1)) if pm else None
        tm4 = re.search(r'<title[^>]*>([^<]+)</title>', h4, re.I)
        fp["card_after_verify"]["title"] = tm4.group(1).strip()[:50] if tm4 else None
    log.info("FP card_after_verify: %s", fp["card_after_verify"])
except Exception as e:
    fp["verify_err"] = str(e)[:80]

# ═══════════════════════════════════════════
# 3. OZON SESSION STRATEGY
# ═══════════════════════════════════════════
log.info("--- OZON session ---")
oz = {}
sess_oz = cffi.Session(impersonate="chrome")
lid = "3481320800"

# O1: Homepage first (get cookies)
try:
    t1 = time.monotonic()
    r0 = sess_oz.get("https://www.ozon.ru/", timeout=12)
    oz["home"] = {"http": r0.status_code, "len": len(r0.text),
                  "cookies": list(sess_oz.cookies.keys()), "t": round(time.monotonic()-t1,1)}
    log.info("OZ home: %s", oz["home"])
except Exception as e:
    oz["home"] = {"err": str(e)[:80]}

# O2: Product in same session
try:
    time.sleep(1)
    t1 = time.monotonic()
    r1 = sess_oz.get(f"https://www.ozon.ru/product/{lid}/", timeout=15,
                     headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                              "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                              "sec-ch-ua": '"Google Chrome";v="131","Chromium";v="131","Not_A Brand";v="24"',
                              "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
                              "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                              "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
                              "Upgrade-Insecure-Requests": "1"})
    html = r1.text
    oz["product"] = {"http": r1.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r1.status_code == 200 and len(html) > 5000:
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        og_t = re.search(r'property="og:title"\s+content="([^"]*)"', html)
        tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
        oz["product"].update({"og_price": og_p.group(1) if og_p else None,
                               "og_title": og_t.group(1)[:50] if og_t else None,
                               "title_tag": tm.group(1).strip()[:60] if tm else None})
        # data-widget
        dw = list(set(re.findall(r'data-widget="([^"]+)"', html)))[:10]
        oz["product"]["widgets"] = dw
        # state
        st = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{)', html)
        oz["product"]["has_state"] = bool(st)
    elif r1.status_code == 403:
        # Check 403 body for hints
        oz["product"]["body_snippet"] = html[:300]
    log.info("OZ product: %s", oz["product"])
except Exception as e:
    oz["product"] = {"err": str(e)[:80]}

# O3: Composer in same session
try:
    t1 = time.monotonic()
    r2 = sess_oz.get(f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{lid}/",
                     timeout=12, verify=False,
                     headers={"Accept": "application/json", "Referer": "https://www.ozon.ru/",
                              "Accept-Language": "ru-RU,ru;q=0.9"})
    oz["composer"] = {"http": r2.status_code, "len": len(r2.content), "t": round(time.monotonic()-t1,1)}
    if r2.status_code == 200:
        d = r2.json()
        oz["composer"]["keys"] = list(d.keys())[:8] if isinstance(d, dict) else []
    log.info("OZ composer: %s", oz["composer"])
except Exception as e:
    oz["composer"] = {"err": str(e)[:80]}

# O4: Try entrypoint with session cookies
try:
    t1 = time.monotonic()
    r3 = sess_oz.get(f"https://www.ozon.ru/api/entrypoint/pdp/{lid}", timeout=12,
                     headers={"Accept": "application/json",
                              "Referer": f"https://www.ozon.ru/product/{lid}/"})
    oz["entrypoint"] = {"http": r3.status_code, "len": len(r3.content), "t": round(time.monotonic()-t1,1)}
    if r3.status_code == 200:
        d = r3.json()
        oz["entrypoint"]["keys"] = list(d.keys())[:10] if isinstance(d, dict) else []
    log.info("OZ entrypoint: %s", oz["entrypoint"])
except Exception as e:
    oz["entrypoint"] = {"err": str(e)[:80]}

R["ozon"] = oz

# ═══════════════════════════════════════════
# 4. AVITO 439 ANALYSIS + SESSION
# ═══════════════════════════════════════════
log.info("--- AVITO ---")
av = {}
sess_av = cffi.Session(impersonate="chrome")

# A1: Homepage first
try:
    t1 = time.monotonic()
    r0 = sess_av.get("https://www.avito.ru/", timeout=12)
    av["home"] = {"http": r0.status_code, "len": len(r0.text),
                  "cookies": list(sess_av.cookies.keys()), "t": round(time.monotonic()-t1,1)}
    log.info("AV home: %s", av["home"])
except Exception as e:
    av["home"] = {"err": str(e)[:80]}

# A2: Search in session
try:
    time.sleep(1)
    t1 = time.monotonic()
    r1 = sess_av.get("https://www.avito.ru/vladivostok/smartfony", timeout=15,
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
        av["search"]["first_titles"] = [t.strip()[:40] for t in titles[:3]]
    elif r1.status_code in (403, 429, 439):
        av["search"]["blocked"] = True
        # Save 439 page
        (BASE / "diag_v11_av_439.html").write_text(html, encoding="utf-8")
        av["search"]["saved"] = True
        # Analyze
        av["search"]["captcha"] = "captcha" in html.lower()
        av["search"]["antibot"] = "antibot" in html.lower()
        # Look for challenge
        ch = re.findall(r'(?:challenge|verify|check|token|nonce)[^;]{0,80}', html, re.I)
        av["search"]["challenge_hints"] = ch[:5]
        # Scripts
        sc = re.findall(r'<script[^>]*src=["\']([^"\']*)["\']', html, re.I)
        av["search"]["script_srcs"] = sc[:5]
    log.info("AV search: %s", av["search"])
except Exception as e:
    av["search"] = {"err": str(e)[:80]}

# A3: Try different category (less popular = less protection?)
try:
    t1 = time.monotonic()
    r2 = sess_av.get("https://www.avito.ru/vladivostok/nedvizhimost/prodazha-kvartir", timeout=12,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU"})
    av["cat2"] = {"http": r2.status_code, "len": len(r2.text), "t": round(time.monotonic()-t1,1)}
    if r2.status_code == 200:
        prices = re.findall(r'data-price="(\d+)"', r2.text)
        av["cat2"]["n_prices"] = len(prices)
    log.info("AV cat2: %s", av["cat2"])
except Exception as e:
    av["cat2"] = {"err": str(e)[:80]}

# A4: Avito card with session
try:
    t1 = time.monotonic()
    r3 = sess_av.get("https://www.avito.ru/ad/4097975275", timeout=12,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU"})
    html = r3.text
    av["card"] = {"http": r3.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r3.status_code == 200 and len(html) > 3000:
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
out = BASE / "diag_v11_results.json"
out.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
log.info("=== V11 DONE t=%.1fs ===", R["total_s"])
log.info("→ %s", out)
