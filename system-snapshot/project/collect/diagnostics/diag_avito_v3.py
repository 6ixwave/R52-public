"""Avito v3: 90s budget. Decode 439 body, find real categories, extract ad IDs."""
import json, time, logging, re, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("avito3")

from curl_cffi import requests as cr

HH = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
      "Referer": "https://www.avito.ru/"}

results = {}
t0 = time.monotonic()

def g(label, url, hdrs=HH, to=12):
    el = time.monotonic() - t0
    if el > 85:
        log.info("BUDGET at %s", label)
        return 0, None
    try:
        r = cr.get(url, headers=hdrs, impersonate="chrome", timeout=to)
        log.info("%-25s %d %7dB", label, r.status_code, len(r.content))
        return r.status_code, r
    except Exception as e:
        log.info("%-25s ERR %s", label, str(e)[:70])
        return 0, None

# === 1) DECODE THE 439 PAGE (14KB — what's in it?) ===
log.info("=== 1) DECODE 439 BODY ===")
u = "https://www.avito.ru/moscow/transport"
st, r = g("cat_439", u)
if st in (200, 429, 439, 403) and r:
    body = r.text
    results["cat_439"] = {"status": st, "size": len(body)}
    
    # Look for embedded JSON / state
    for pat_name, pat in [
        ("preloaded", r'window\.__PRELOADED_STATE__\s*=\s*(\{.+?\})\s*;'),
        ("initial", r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*;'),
        ("next", r'__NEXT_DATA__.*?(\{.+?\})\s*</script>'),
    ]:
        m = re.search(pat, body, re.DOTALL)
        if m:
            results["cat_439"]["state"] = pat_name
            log.info("  *** state found: %s", pat_name)
            break
    
    # Ad IDs in any form
    ids = re.findall(r'/ad/(\d{6,})', body)
    if not ids:
        ids = re.findall(r'"(?:adId|id)"\s*:\s*"?(\d{8,12})"?', body)
    if not ids:
        ids = re.findall(r'data-ad-id=["\'](\d+)', body)
    results["cat_439"]["ids"] = list(dict.fromkeys(ids))[:20]
    log.info("  *** IDs: %s", results["cat_439"]["ids"][:5])
    
    # Title / meta
    tm = re.search(r'<title[^>]*>([^<]+)', body, re.I)
    if tm:
        results["cat_439"]["title"] = tm.group(1)
    
    # First 500 chars for context
    results["cat_439"]["snippet"] = body[:500]
    
    # Check if it's a "wait" page
    if "wait" in body.lower()[:2000] or "подождите" in body.lower()[:2000]:
        results["cat_439"]["is_wait"] = True
        log.info("  *** WAIT page detected")

# === 2) TRY MORE CATEGORIES ===
log.info("=== 2) MORE CATEGORIES ===")
cats = [
    ("moscow_auto", "https://www.avito.ru/moscow/avto"),
    ("moscow_realty", "https://www.avito.ru/moscow/nedvizhimost"),
    ("moscow_work", "https://www.avito.ru/moscow/trud"),
    ("spb_auto", "https://www.avito.ru/spb/avto"),
    ("ekb_transport", "https://www.avito.ru/ekaterinburg/transport"),
]
found_ids = results.get("cat_439", {}).get("ids", [])
for lbl, url in cats:
    if time.monotonic() - t0 > 50:
        break
    st, r = g(f"cat_{lbl}", url)
    if st in (200, 429, 439) and r and len(r.content) > 5000:
        body = r.text
        ids = re.findall(r'/ad/(\d{6,})', body)
        if not ids:
            ids = re.findall(r'"(?:adId|id)"\s*:\s*"?(\d{8,12})"?', body)
        if not ids:
            ids = re.findall(r'data-ad-id=["\'](\d+)', body)
        ids = list(dict.fromkeys(ids))[:15]
        results[f"cat_{lbl}"] = {"status": st, "ids": ids, "size": len(r.content)}
        log.info("  *** %s: %d IDs", lbl, len(ids))
        if ids and not found_ids:
            found_ids = ids
            break
    time.sleep(2)

# === 3) FETCH REAL AD (if found) ===
if found_ids:
    log.info("=== 3) FETCH AD %s ===", found_ids[0])
    aid = found_ids[0]
    card_urls = [
        ("short", f"https://www.avito.ru/ad/{aid}"),
        ("moscow", f"https://www.avito.ru/moscow/{aid}"),
    ]
    for lbl, url in card_urls:
        if time.monotonic() - t0 > 75:
            break
        st, r = g(f"ad_{lbl}", url)
        if st == 200 and r and len(r.content) > 3000:
            html = r.text
            key = f"ad_{aid}"
            results[key] = {"url": url, "size": len(html), "status": st}
            
            # CAPTCHA?
            lower = html[:10000].lower()
            captcha = any(m in lower for m in ["smartcaptcha","recaptcha","cf-challenge","turnstile","data-sitekey"])
            results[key]["captcha"] = captcha
            
            # JSON-LD
            for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.I|re.DOTALL):
                try:
                    ld = json.loads(m.group(1))
                    results[key]["jsonld"] = ld
                    log.info("  *** JSON-LD: %s", json.dumps(ld, ensure_ascii=False)[:200])
                except: pass
            
            # State
            for pat in [r'window\.__PRELOADED_STATE__\s*=\s*(\{.+?\})\s*;',
                        r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*;']:
                m = re.search(pat, html, re.DOTALL)
                if m:
                    try:
                        st_data = json.loads(m.group(1))
                        results[key]["state_keys"] = list(st_data.keys())[:15]
                        ad = st_data.get("ad") or st_data.get("data",{}).get("ad") or {}
                        if ad:
                            results[key]["ad_name"] = ad.get("name","")
                            results[key]["ad_price"] = ad.get("price") or ad.get("priceValue")
                            results[key]["ad_desc"] = (ad.get("description") or "")[:200]
                        log.info("  *** State: %s", ad.get("name","?")[:50])
                    except Exception as e:
                        log.info("  *** State parse: %s", str(e)[:50])
                    break
            
            # og: meta
            for prop in ["og:title","og:description","og:image","og:price:amount"]:
                m = re.search(rf'property=["\']{prop}["\'][^>]*content=["\']([^"\']+)', html, re.I)
                if m:
                    results[key][prop] = m.group(1)[:200]
            
            # Prices
            prices = re.findall(r'(\d{3,9}[.,]?\d{0,2})\s*(?:₽|руб)', html)
            if prices:
                results[key]["prices"] = prices[:8]
            
            # Images
            imgs = re.findall(r'https?://[^"\'\s]*(?:avatars\.mmedia\.avito\.st|l\d+\.avito\.st|avito\.st)[^"\'\s]*', html)
            if imgs:
                results[key]["images"] = list(dict.fromkeys(imgs))[:8]
            
            break
        time.sleep(2)
else:
    log.info("=== 3) NO REAL IDs FOUND, trying direct ===")
    # Try a very common pattern: recent high-ID ads
    for aid in ["4712345678", "4709876543", "4705432198"]:
        if time.monotonic() - t0 > 75:
            break
        st, r = g(f"ad_{aid}", f"https://www.avito.ru/ad/{aid}")
        if st == 200 and r and len(r.content) > 3000:
            results[f"ad_{aid}"] = {"size": len(r.content), "status": st,
                                    "title": re.search(r'<title[^>]*>([^<]+)', r.text, re.I).group(1) if re.search(r'<title[^>]*>([^<]+)', r.text, re.I) else "?"}
            break

# === SAVE ===
out = Path(__file__).parent / "diag_avito_v3_out.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("=== DONE %.0fs. %d keys ===", time.monotonic()-t0, len(results))
