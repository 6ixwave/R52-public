"""V13: WB shard fix + FP deep parse + OZ firefox + AV delay. Target <65s."""
import json, os, sys, time, logging, re, traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
TZ = timezone(timedelta(hours=10))
NOW = lambda: datetime.now(TZ).isoformat()

LOGF = BASE / "diag_v13_out.txt"
hf = logging.FileHandler(LOGF, mode="w", encoding="utf-8")
hc = logging.StreamHandler(sys.stdout)
fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
hf.setFormatter(fmt); hc.setFormatter(fmt)
log = logging.getLogger("V13")
log.setLevel(logging.INFO)
log.addHandler(hf); log.addHandler(hc)

R = {}
t0 = time.monotonic()
log.info("=== V13 START ===")

from curl_cffi import requests as cffi

# ═══════════════════════════════════════════
# WB: SHARD FIX (nm%10) + session
# ═══════════════════════════════════════════
log.info("--- WB ---")
nm = 1097975275
vol, part = nm // 100000, nm // 1000
shard = nm % 10
cpath = f"/vol{vol}/part{part}/{nm}/info/ru/card.json"
H_json = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}
wb = {}
log.info("WB: nm=%d vol=%d part=%d shard=%d", nm, vol, part, shard)

# W1: Correct shard (static-basket-05)
for host in (f"static-basket-{shard:02d}.wbbasket.ru",
             f"basket-{shard:02d}.wbbasket.ru",
             f"static-basket-{shard}.wbbasket.ru"):
    try:
        t1 = time.monotonic()
        r = cffi.get(f"https://{host}{cpath}", impersonate="chrome", timeout=6, headers=H_json)
        el = time.monotonic() - t1
        key = host.split(".")[0]
        wb[key] = {"http": r.status_code, "len": len(r.content), "t": round(el,1)}
        if r.status_code == 200:
            d = r.json()
            wb[key].update({
                "title": d.get("imt_name","")[:80],
                "brand": d.get("selling",{}).get("brand_name",""),
                "opts": len(d.get("options",[])),
                "imgs": len(d.get("images",[])),
                "char": d.get("characteristics",{}).get("value") if isinstance(d.get("characteristics"),dict) else None,
            })
            log.info("WB %s: OK '%s' brand=%s opts=%d imgs=%d", key,
                     d.get("imt_name","")[:40], d.get("selling",{}).get("brand_name",""),
                     len(d.get("options",[])), len(d.get("images",[])))
            break
        else:
            log.info("WB %s: HTTP %d len=%d", key, r.status_code, len(r.content))
    except Exception as e:
        wb[host.split(".")[0]] = {"err": str(e)[:60], "t": round(time.monotonic()-t1,1)}
        log.info("WB %s: %s", host.split(".")[0], str(e)[:50])

# W2: Try multiple shards (0-9) quickly for basket-01
log.info("WB: scanning shards 0-9...")
shard_scan = {}
for s in range(10):
    host = f"static-basket-{s:02d}.wbbasket.ru"
    try:
        t1 = time.monotonic()
        r = cffi.get(f"https://{host}{cpath}", impersonate="chrome", timeout=4, headers=H_json)
        shard_scan[s] = r.status_code
        if r.status_code == 200:
            d = r.json()
            shard_scan[s] = {"http": 200, "title": d.get("imt_name","")[:40]}
            log.info("WB shard %d: HIT '%s'", s, d.get("imt_name","")[:30])
    except Exception as e:
        shard_scan[s] = str(e)[:30]
    time.sleep(0.1)
wb["shard_scan"] = shard_scan
log.info("WB shard scan: %s", shard_scan)

# W3: Session approach (main → detail → API)
try:
    sess = cffi.Session(impersonate="chrome")
    t1 = time.monotonic()
    r0 = sess.get("https://www.wildberries.ru/", timeout=8)
    log.info("WB main: HTTP=%d cookies=%s t=%.1fs", r0.status_code, list(sess.cookies.keys()), time.monotonic()-t1)
    time.sleep(0.5)
    t1 = time.monotonic()
    r1 = sess.get(f"https://www.wildberries.ru/catalog/{nm}/detail.aspx", timeout=10,
                  headers={"Accept": "text/html", "Accept-Language": "ru-RU"})
    el = time.monotonic() - t1
    html = r1.text
    wb["sess_html"] = {"http": r1.status_code, "len": len(html), "t": round(el,1)}
    if r1.status_code == 200 and len(html) > 5000:
        og_t = re.search(r'property="og:title"\s+content="([^"]*)"', html)
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        wb["sess_html"].update({"og_title": og_t.group(1)[:60] if og_t else None,
                                "og_price": og_p.group(1) if og_p else None})
        log.info("WB sess_html: title=%s price=%s", wb["sess_html"].get("og_title"), wb["sess_html"].get("og_price"))
    elif r1.status_code == 498:
        # Save 498 page
        (BASE / "diag_v13_wb_498.html").write_text(html, encoding="utf-8")
        wb["sess_html"]["498_saved"] = True
        # Analyze
        wb["sess_html"]["498_len"] = len(html)
        wb["sess_html"]["498_title"] = (re.search(r'<title[^>]*>([^<]+)</title>', html, re.I) or [None,""])[1][:60] if re.search(r'<title[^>]*>([^<]+)</title>', html, re.I) else None
        wb["sess_html"]["498_scripts"] = re.findall(r'<script[^>]*src=["\']([^"\']*)["\']', html, re.I)[:5]
        log.info("WB 498: len=%d title=%s", len(html), wb["sess_html"].get("498_title"))
    # API in session
    t1 = time.monotonic()
    r2 = sess.get(f"https://card.wb.ru/cards/v2/detail?nm={nm}", timeout=8,
                  headers={"Accept": "application/json", "Referer": f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"})
    wb["sess_api"] = {"http": r2.status_code, "len": len(r2.content), "t": round(time.monotonic()-t1,1)}
    if r2.status_code == 200:
        items = r2.json().get("data",{}).get("items",[])
        if items:
            p = items[0].get("price",{})
            wb["sess_api"].update({"basic": p.get("basic"), "product": p.get("product"),
                                   "sale": p.get("sale"), "total": p.get("total")})
    log.info("WB sess_api: %s", wb["sess_api"])
except Exception as e:
    wb["sess_err"] = str(e)[:100]
    log.info("WB sess err: %s", e)

R["wb"] = wb

# ═══════════════════════════════════════════
# FARPOST: Deep parse + vladivostok product
# ═══════════════════════════════════════════
log.info("--- FARPOST ---")
fp = {}
sess_fp = cffi.Session(impersonate="chrome")

# F1: Warm-up
t1 = time.monotonic()
sess_fp.get("https://www.farpost.ru/", timeout=8)
log.info("FP warm: t=%.1fs cookies=%s", time.monotonic()-t1, list(sess_fp.cookies.keys()))
time.sleep(1.5)

# F2: Parse card 139122082 (krasnoyarsk - known working)
def parse_fp_card(html, url):
    """Extract all data from FP card page."""
    d = {"url": url[:80], "len": len(html)}
    # JSON-LD
    lds = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
    d["n_ld"] = len(lds)
    for ld in lds:
        try:
            o = json.loads(ld.strip())
            if isinstance(o, dict) and o.get("@type") == "Product":
                d["ld_name"] = o.get("name","")[:80]
                d["ld_desc"] = (o.get("description","") or "")[:100]
                off = o.get("offers", {})
                d["ld_price"] = off.get("price")
                d["ld_currency"] = off.get("priceCurrency")
                d["ld_availability"] = off.get("availability","")[:50]
                d["ld_imgs"] = off.get("image", [])
                if isinstance(d["ld_imgs"], str):
                    d["ld_imgs"] = [d["ld_imgs"]]
                d["n_ld_imgs"] = len(d["ld_imgs"])
            elif isinstance(o, list):
                for i in o:
                    if isinstance(i, dict) and i.get("@type") == "Product":
                        d["ld_name"] = i.get("name","")[:80]
                        off = i.get("offers", {})
                        d["ld_price"] = off.get("price")
                        d["ld_currency"] = off.get("priceCurrency")
                        d["ld_imgs"] = off.get("image", [])
                        if isinstance(d["ld_imgs"], str):
                            d["ld_imgs"] = [d["ld_imgs"]]
                        d["n_ld_imgs"] = len(d["ld_imgs"])
                        break
        except: pass
    # data-bulletin-price
    pm = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
    d["attr_price"] = int(pm.group(1)) if pm else None
    # og tags
    og_t = re.search(r'property="og:title"\s+content="([^"]*)"', html)
    og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
    og_img = re.search(r'property="og:image"\s+content="([^"]*)"', html)
    d["og_title"] = og_t.group(1)[:80] if og_t else None
    d["og_price"] = og_p.group(1) if og_p else None
    d["og_image"] = og_img.group(1)[:80] if og_img else None
    # Title tag
    tm = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
    d["title_tag"] = tm.group(1).strip()[:80] if tm else None
    # H1
    h1 = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    if h1:
        d["h1"] = re.sub(r'<[^>]+>','',h1.group(1)).strip()[:80]
    # Photos
    photos = re.findall(r'"(https?://static\.baza\.farpost\.ru/[^"]+)"', html)
    d["n_photos"] = len([p for p in photos if not any(s in p for s in ('.svg','.gif','.ico','_thumbnail'))])
    d["first_photos"] = [p for p in photos if not any(s in p for s in ('.svg','.gif','.ico','_thumbnail'))][:3]
    # Specs (data-field)
    specs = re.findall(r'data-field="([^"]+)"[^>]*>([^<]{2,100})<', html)
    d["n_specs"] = len(specs)
    d["specs_sample"] = specs[:5]
    # Bulletin ID
    bm = re.search(r'data-bulletin-id=["\'](\d+)["\']', html)
    d["bulletin_id"] = bm.group(1) if bm else None
    # Category
    cat = re.search(r'data-category[^>]*>([^<]+)<', html)
    d["category"] = cat.group(1).strip()[:40] if cat else None
    return d

# Card 1
t1 = time.monotonic()
r = sess_fp.get("https://www.farpost.ru/vladivostok/139122082.html", timeout=12)
html = r.text
fp["card1"] = parse_fp_card(html, str(r.url))
fp["card1"]["http"] = r.status_code
fp["card1"]["t"] = round(time.monotonic()-t1, 1)
log.info("FP card1: %s", {k:v for k,v in fp["card1"].items() if k not in ("first_photos","specs_sample")})

# Card 2: Try a vladivostok product (find from category)
time.sleep(1)
t1 = time.monotonic()
r_cat = sess_fp.get("https://www.farpost.ru/vladivostok/electronics", timeout=12)
cat_html = r_cat.text
fp["cat"] = {"http": r_cat.status_code, "len": len(cat_html), "t": round(time.monotonic()-t1,1)}
log.info("FP cat: HTTP=%d len=%d", r_cat.status_code, len(cat_html))

# Parse category for links
cat_links = re.findall(r'href="(/vladivostok/[^"]*-\d+\.html)"', cat_html)
fp["cat"]["n_links"] = len(cat_links)
fp["cat"]["first_links"] = cat_links[:5]
# Prices in category
cat_prices = re.findall(r'data-bulletin-price=["\'](\d+)["\']', cat_html)
fp["cat"]["n_prices"] = len(cat_prices)
fp["cat"]["first_prices"] = cat_prices[:5]
# Item titles
cat_titles = re.findall(r'class="[^"]*item[^"]*title[^"]*"[^>]*>([^<]+)<', cat_html)
fp["cat"]["n_titles"] = len(cat_titles)
fp["cat"]["first_titles"] = [t.strip()[:40] for t in cat_titles[:5]]
log.info("FP cat: links=%d prices=%d titles=%d", len(cat_links), len(cat_prices), len(cat_titles))

# If we found a vladivostok link, parse it
if cat_links:
    vl_link = cat_links[0]
    t1 = time.monotonic()
    r2 = sess_fp.get(f"https://www.farpost.ru{vl_link}", timeout=12)
    fp["card2"] = parse_fp_card(r2.text, str(r2.url))
    fp["card2"]["http"] = r2.status_code
    fp["card2"]["t"] = round(time.monotonic()-t1, 1)
    log.info("FP card2: %s", {k:v for k,v in fp["card2"].items() if k not in ("first_photos","specs_sample")})

# F3: Save full card HTML for reference
(BASE / "diag_v13_fp_card.html").write_text(html, encoding="utf-8")
log.info("FP card saved: %d bytes", len(html))

R["farpost"] = fp

# ═══════════════════════════════════════════
# OZON: Try firefox + safari + different paths
# ═══════════════════════════════════════════
log.info("--- OZON ---")
oz = {}
lid = "3481320800"

# O1: Firefox impersonation
try:
    t1 = time.monotonic()
    r = cffi.get(f"https://www.ozon.ru/product/{lid}/", impersonate="firefox", timeout=12,
                 headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    oz["firefox"] = {"http": r.status_code, "len": len(r.text), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200:
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', r.text)
        oz["firefox"]["og_price"] = og_p.group(1) if og_p else None
    log.info("OZ firefox: %s", oz["firefox"])
except Exception as e:
    oz["firefox"] = {"err": str(e)[:60]}

# O2: Safari impersonation
try:
    t1 = time.monotonic()
    r = cffi.get(f"https://www.ozon.ru/product/{lid}/", impersonate="safari", timeout=12,
                 headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"})
    oz["safari"] = {"http": r.status_code, "len": len(r.text), "t": round(time.monotonic()-t1,1)}
    if r.status_code == 200:
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', r.text)
        oz["safari"]["og_price"] = og_p.group(1) if og_p else None
    log.info("OZ safari: %s", oz["safari"])
except Exception as e:
    oz["safari"] = {"err": str(e)[:60]}

# O3: Chrome with session (home → product)
try:
    sess_oz = cffi.Session(impersonate="chrome")
    t1 = time.monotonic()
    r0 = sess_oz.get("https://www.ozon.ru/", timeout=10)
    oz["sess_home"] = {"http": r0.status_code, "cookies": list(sess_oz.cookies.keys()), "t": round(time.monotonic()-t1,1)}
    time.sleep(1)
    t1 = time.monotonic()
    r1 = sess_oz.get(f"https://www.ozon.ru/product/{lid}/", timeout=12)
    html = r1.text
    oz["sess_product"] = {"http": r1.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r1.status_code == 200 and len(html) > 5000:
        og_p = re.search(r'property="og:price:amount"\s+content="([^"]*)"', html)
        og_t = re.search(r'property="og:title"\s+content="([^"]*)"', html)
        oz["sess_product"].update({"og_price": og_p.group(1) if og_p else None,
                                    "og_title": og_t.group(1)[:60] if og_t else None})
        # data-widget
        dw = list(set(re.findall(r'data-widget="([^"]+)"', html)))[:8]
        oz["sess_product"]["widgets"] = dw
    elif r1.status_code == 403:
        # Save 403 body
        (BASE / "diag_v13_oz_403.html").write_text(html, encoding="utf-8")
        oz["sess_product"]["403_saved"] = True
        # Check for JS challenge
        oz["sess_product"]["has_js"] = "<script" in html
        oz["sess_product"]["scripts"] = re.findall(r'<script[^>]*src=["\']([^"\']*)["\']', html, re.I)[:5]
    log.info("OZ sess: home=%s product=%s", oz["sess_home"].get("http"), oz["sess_product"].get("http"))
except Exception as e:
    oz["sess_err"] = str(e)[:80]

# O4: Try different URL patterns
try:
    t1 = time.monotonic()
    # Pattern: /product/{lid} (no trailing slash)
    r = cffi.get(f"https://www.ozon.ru/product/{lid}", impersonate="chrome", timeout=10,
                 headers={"Accept": "text/html", "Accept-Language": "ru-RU"})
    oz["no_slash"] = {"http": r.status_code, "len": len(r.text), "t": round(time.monotonic()-t1,1)}
    # Pattern: /search/?text=...
    t1 = time.monotonic()
    r2 = cffi.get(f"https://www.ozon.ru/search/?text=iphone&from_global=true", impersonate="chrome", timeout=10,
                  headers={"Accept": "text/html", "Accept-Language": "ru-RU"})
    oz["search"] = {"http": r2.status_code, "len": len(r2.text), "t": round(time.monotonic()-t1,1)}
    log.info("OZ no_slash: %s, search: %s", oz["no_slash"]["http"], oz["search"]["http"])
except Exception as e:
    oz["pattern_err"] = str(e)[:60]

R["ozon"] = oz

# ═══════════════════════════════════════════
# AVITO: Longer delays + different approach
# ═══════════════════════════════════════════
log.info("--- AVITO ---")
av = {}
sess_av = cffi.Session(impersonate="chrome")

# A1: Wait 3s, then homepage
time.sleep(3)
try:
    t1 = time.monotonic()
    r0 = sess_av.get("https://www.avito.ru/", timeout=12)
    av["home"] = {"http": r0.status_code, "len": len(r0.text),
                  "cookies": list(sess_av.cookies.keys()), "t": round(time.monotonic()-t1,1)}
    log.info("AV home: %s", av["home"])
except Exception as e:
    av["home"] = {"err": str(e)[:60]}

# A2: Wait 3s, then search
time.sleep(3)
try:
    t1 = time.monotonic()
    r1 = sess_av.get("https://www.avito.ru/vladivostok/smartfony", timeout=12,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU"})
    html = r1.text
    av["search"] = {"http": r1.status_code, "len": len(html), "t": round(time.monotonic()-t1,1)}
    if r1.status_code == 200:
        prices = re.findall(r'data-price="(\d+)"', html)
        av["search"]["n_prices"] = len(prices)
        av["search"]["first_prices"] = prices[:5]
    elif r1.status_code in (403, 429, 439):
        av["search"]["blocked"] = True
        # Check for specific challenge
        av["search"]["has_iframe"] = "<iframe" in html
        av["search"]["has_form"] = "<form" in html
        av["search"]["title"] = (re.search(r'<title[^>]*>([^<]+)</title>', html, re.I) or [None,""])[1][:60] if re.search(r'<title[^>]*>([^<]+)</title>', html, re.I) else None
    log.info("AV search: %s", av["search"])
except Exception as e:
    av["search"] = {"err": str(e)[:60]}

# A3: Try less popular category
time.sleep(2)
try:
    t1 = time.monotonic()
    r2 = sess_av.get("https://www.avito.ru/vladivostok/dacha_sad/posuda", timeout=12,
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU"})
    av["cat2"] = {"http": r2.status_code, "len": len(r2.text), "t": round(time.monotonic()-t1,1)}
    if r2.status_code == 200:
        prices = re.findall(r'data-price="(\d+)"', r2.text)
        av["cat2"]["n_prices"] = len(prices)
    log.info("AV cat2: %s", av["cat2"])
except Exception as e:
    av["cat2"] = {"err": str(e)[:60]}

# A4: Avito API v2 (internal)
time.sleep(1)
try:
    t1 = time.monotonic()
    r3 = sess_av.get("https://www.avito.ru/api/search/listings",
                     params={"query": "iphone", "city": "vladivostok", "limit": "20", "sort": "date_desc"},
                     timeout=12,
                     headers={"Accept": "application/json", "Accept-Language": "ru-RU",
                              "X-Requested-With": "XMLHttpRequest"})
    av["api"] = {"http": r3.status_code, "len": len(r3.content), "t": round(time.monotonic()-t1,1)}
    if r3.status_code == 200:
        d = r3.json()
        av["api"]["keys"] = list(d.keys())[:8] if isinstance(d, dict) else []
        items = d.get("items", d.get("listings", []))
        av["api"]["n_items"] = len(items) if isinstance(items, list) else 0
    log.info("AV api: %s", av["api"])
except Exception as e:
    av["api"] = {"err": str(e)[:60]}

R["avito"] = av

# ═══════════════════════════════════════════
# Finalize
# ═══════════════════════════════════════════
R["total_s"] = round(time.monotonic() - t0, 1)
R["time"] = NOW()
out = BASE / "diag_v13_results.json"
out.write_text(json.dumps(R, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
log.info("=== V13 DONE t=%.1fs ===", R["total_s"])
log.info("→ %s", out)
