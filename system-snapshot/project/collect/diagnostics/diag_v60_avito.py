"""Avito v60: Session-based mobile + desktop.
Strategy:
A. Mobile: warm-up session → ad page (loading) → JS redirect → final ad page
B. Desktop: warm-up session → direct ad URL
C. Parse whatever real HTML we get: title, price, images, JSON-LD, description
"""
import json, time, logging, re, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("avito60")

from curl_cffi import requests as cr

results = {}
t0 = time.monotonic()

def budget_ok(label):
    el = time.monotonic() - t0
    if el > 85:
        log.info("BUDGET at %s", label)
        return False
    return True

# Test ads from previous diagnostics
AD_IDS = ["8341658309", "7764972140", "1154321987"]
AD_SLUGS = {
    "8341658309": "moskva/telefony/iphone_14_128_gb",
    "7764972140": "novosemeykino/zemelnye_uchastki/uchastok_494_ga_promnaznacheniya",
    "1154321987": "moskva/telefony/iphone",
}

# ============================================================
# PART A: Mobile session (curl_cffi, impersonate=chrome)
# ============================================================
log.info("=== A: MOBILE SESSION ===")
sess = cr.Session(impersonate="chrome")

# A1: Warm up — m.avito.ru homepage (gets cookies)
if budget_ok("A1"):
    try:
        r = sess.get("https://m.avito.ru/", timeout=12)
        log.info("A1 home: %d %dB", r.status_code, len(r.content))
        results["a1_home"] = {"status": r.status_code, "size": len(r.content)}
    except Exception as e:
        log.info("A1 home ERR: %s", str(e)[:80])
        results["a1_home"] = {"error": str(e)[:100]}
    time.sleep(2)

# A2: Fetch ad pages (expect loading page with meta refresh + JS)
ad_html = None
ad_final_url = None
for aid in AD_IDS:
    if not budget_ok(f"A2_{aid}"):
        break
    slug = AD_SLUGS.get(aid, f"moskva/telefony/ad_{aid}")
    url = f"https://m.avito.ru/{slug}_{aid}"
    try:
        r = sess.get(url, timeout=15)
        log.info("A2 ad %s: %d %dB", aid, r.status_code, len(r.content))
        results[f"a2_{aid}"] = {"status": r.status_code, "size": len(r.content), "url": url}

        if r.status_code == 200 and len(r.content) > 5000:
            body = r.text
            is_loading = ("Подождите" in body or 'meta http-equiv="refresh"' in body)

            if is_loading:
                log.info("A2 %s: LOADING PAGE, extracting JS...", aid)
                # Extract all script src
                js_urls = re.findall(r'<script\s+src=["\']([^"\']+)["\']', body)
                log.info("A2 scripts: %s", js_urls)
                results[f"a2_{aid}_scripts"] = js_urls

                for js_rel in js_urls:
                    if not budget_ok(f"A2_js_{aid}"):
                        break
                    js_url = js_rel if js_rel.startswith("http") else "https://m.avito.ru" + js_rel
                    time.sleep(1)
                    try:
                        r_js = sess.get(js_url, timeout=10)
                        log.info("A2 JS %s: %d %dB", js_rel[:40], r_js.status_code, len(r_js.content))
                        js_body = r_js.text
                        results[f"a2_{aid}_js_{js_rel[:20]}"] = {
                            "status": r_js.status_code,
                            "size": len(js_body),
                            "head": js_body[:400],
                        }

                        # Look for redirect patterns in JS
                        redir_patterns = [
                            r'(?:location\.href|window\.location(?:\.href)?|document\.location(?:\.href)?)\s*=\s*["\']([^"\']+)["\']',
                            r'window\.location\s*=\s*["\']([^"\']+)["\']',
                            r'redirect\s*[:=]\s*["\']([^"\']+)["\']',
                            r'["\']([^"\']*avito\.ru[^"\']+)["\']',
                        ]
                        target = None
                        for pat in redir_patterns:
                            m = re.search(pat, js_body)
                            if m:
                                target = m.group(1)
                                break
                        if target:
                            log.info("A2 REDIRECT: %s", target[:100])
                            results[f"a2_{aid}_redirect"] = target
                            time.sleep(1.5)
                            r2 = sess.get(target, timeout=15)
                            log.info("A2 follow: %d %dB", r2.status_code, len(r2.content))
                            if r2.status_code == 200 and len(r2.content) > 5000:
                                ad_html = r2.text
                                ad_final_url = target
                                results[f"a2_{aid}_final"] = {"status": 200, "size": len(ad_html), "via": "js_redirect"}
                                break
                    except Exception as e:
                        log.info("A2 JS ERR: %s", str(e)[:80])

                # If JS didn't give redirect, retry the same URL (cookie may have been set)
                if not ad_html:
                    log.info("A2 retry same URL after JS...")
                    time.sleep(2)
                    r2 = sess.get(url, timeout=15)
                    log.info("A2 retry: %d %dB", r2.status_code, len(r2.content))
                    if r2.status_code == 200 and len(r2.content) > 5000:
                        body2 = r2.text
                        if "Подождите" not in body2 and 'meta http-equiv="refresh"' not in body2:
                            ad_html = body2
                            ad_final_url = url
                            results[f"a2_{aid}_final"] = {"status": 200, "size": len(ad_html), "via": "retry"}
                        else:
                            # Try one more time with longer wait
                            time.sleep(3)
                            r3 = sess.get(url, timeout=15)
                            log.info("A2 retry2: %d %dB", r3.status_code, len(r3.content))
                            if r3.status_code == 200 and "Подождите" not in r3.text:
                                ad_html = r3.text
                                ad_final_url = url
                                results[f"a2_{aid}_final"] = {"status": 200, "size": len(ad_html), "via": "retry2"}
                    if ad_html:
                        break
            else:
                # Direct ad page (not loading) — great!
                ad_html = body
                ad_final_url = url
                results[f"a2_{aid}_final"] = {"status": 200, "size": len(ad_html), "via": "direct"}
                break
    except Exception as e:
        log.info("A2 %s ERR: %s", aid, str(e)[:80])
        results[f"a2_{aid}"] = {"error": str(e)[:100]}
    time.sleep(2)

# A3: Parse ad HTML
if ad_html:
    log.info("=== A3: PARSE AD HTML (%dB) ===", len(ad_html))
    ad_data = {"url": ad_final_url}

    # Title
    tm = re.search(r'<title[^>]*>([^<]+)', ad_html, re.I)
    if tm:
        ad_data["title"] = tm.group(1).strip()

    # JSON-LD
    for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', ad_html, re.I | re.DOTALL):
        try:
            ld = json.loads(m.group(1))
            if isinstance(ld, dict):
                t = str(ld.get("@type", ""))
                if any(k in t for k in ("Product", "Offer", "Item", "Ad")):
                    ad_data["jsonld"] = ld
                    break
                if "name" in ld or "offers" in ld:
                    ad_data["jsonld"] = ld
                    break
            elif isinstance(ld, list):
                for item in ld:
                    if isinstance(item, dict) and ("name" in item or "offers" in item):
                        ad_data["jsonld"] = item
                        break
        except (json.JSONDecodeError, ValueError):
            continue

    # Meta tags
    for prop in ["og:title", "og:description", "og:image", "og:url", "og:price:amount", "og:price:currency"]:
        m = re.search(rf'property=["\']{prop}["\'][^>]*content=["\']([^"\']+)', ad_html, re.I)
        if m:
            ad_data[f"meta_{prop}"] = m.group(1)[:300]

    # Prices (multiple patterns)
    prices = re.findall(r'(\d[\d\s\u00a0]{2,12})\s*(?:₽|руб\.?|RUB)', ad_html)
    ad_data["prices_raw"] = prices[:10]
    dp = re.findall(r'data-price=["\'](\d+)["\']', ad_html, re.I)
    if dp:
        ad_data["data_prices"] = dp[:5]
    # Price in JSON-LD
    if "jsonld" in ad_data:
        offers = ad_data["jsonld"].get("offers")
        if isinstance(offers, dict):
            offers = [offers]
        if isinstance(offers, list):
            for o in offers:
                if isinstance(o, dict) and o.get("price"):
                    ad_data["jsonld_price"] = str(o["price"])

    # Images
    imgs = re.findall(r'https?://[^"\'\s\\]*avito\.st[^"\'\s\\]*', ad_html)
    ad_data["images"] = list(dict.fromkeys(imgs))[:15]

    # Description
    desc_m = re.search(r'(?:class|id)=["\'][^"\']*description[^"\']*["\'][^>]*>(.*?)</(?:div|section|p)>', ad_html, re.I | re.DOTALL)
    if desc_m:
        desc_text = re.sub(r'<[^>]+>', ' ', desc_m.group(1))
        desc_text = re.sub(r'\s+', ' ', desc_text).strip()
        if len(desc_text) > 20:
            ad_data["description"] = desc_text[:500]

    # State/JSON in page
    for pat_name, pat in [
        ("preloaded", r'__PRELOADED_STATE__\s*=\s*(\{.+?\})\s*;?\s*(?:</script>|$)'),
        ("initial", r'__INITIAL_STATE__\s*=\s*(\{.+?\})\s*;?\s*(?:</script>|$)'),
        ("next", r'__NEXT_DATA__\s*=\s*(\{.+?\})\s*;?\s*</script>'),
    ]:
        m = re.search(pat, ad_html, re.DOTALL)
        if m:
            ad_data[f"state_{pat_name}"] = {"len": len(m.group(1)), "head": m.group(1)[:300]}
            break

    results["a3_parsed"] = ad_data
    log.info("A3: title=%s, prices=%s, imgs=%d, jsonld=%s",
             ad_data.get("title", "?")[:50],
             ad_data.get("prices_raw", [])[:3],
             len(ad_data.get("images", [])),
             "YES" if "jsonld" in ad_data else "no")

# ============================================================
# PART B: Desktop session (separate cookies)
# ============================================================
log.info("=== B: DESKTOP SESSION ===")
dsess = cr.Session(impersonate="chrome")

if budget_ok("B1"):
    try:
        r = dsess.get("https://www.avito.ru/", timeout=12)
        log.info("B1 home: %d %dB", r.status_code, len(r.content))
        results["b1_home"] = {"status": r.status_code, "size": len(r.content)}
    except Exception as e:
        log.info("B1 ERR: %s", str(e)[:80])
        results["b1_home"] = {"error": str(e)[:100]}
    time.sleep(2)

# B2: Direct ad URLs
for aid in AD_IDS[:2]:
    if not budget_ok(f"B2_{aid}"):
        break
    for pattern in [f"https://www.avito.ru/ad/{aid}", f"https://www.avito.ru/moskva/telefony/ad_{aid}"]:
        try:
            r = dsess.get(pattern, timeout=15)
            log.info("B2 %s: %d %dB", aid, r.status_code, len(r.content))
            results[f"b2_{aid}"] = {"status": r.status_code, "size": len(r.content), "url": pattern}

            if r.status_code == 200 and len(r.content) > 10000:
                body = r.text
                if "Доступ ограничен" not in body and "Подождите" not in body:
                    tm = re.search(r'<title[^>]*>([^<]+)', body, re.I)
                    log.info("B2 REAL: title=%s", tm.group(1)[:60] if tm else "?")
                    results[f"b2_{aid}_real"] = {
                        "title": tm.group(1) if tm else None,
                        "size": len(body),
                    }
                    prices = re.findall(r'(\d[\d\s\u00a0]{2,12})\s*(?:₽|руб)', body)
                    results[f"b2_{aid}_real"]["prices"] = prices[:5]
                    imgs = re.findall(r'https?://[^"\'\s\\]*avito\.st[^"\'\s\\]*', body)
                    results[f"b2_{aid}_real"]["images"] = list(dict.fromkeys(imgs))[:10]
                    break
        except Exception as e:
            log.info("B2 %s ERR: %s", aid, str(e)[:80])
        time.sleep(2)

# ============================================================
# PART C: Try m.avito.ru/ad/{id} (short URL)
# ============================================================
if budget_ok("C"):
    log.info("=== C: SHORT MOBILE URL ===")
    for aid in AD_IDS[:2]:
        url = f"https://m.avito.ru/ad/{aid}"
        try:
            r = sess.get(url, timeout=15)
            log.info("C %s: %d %dB", aid, r.status_code, len(r.content))
            results[f"c_{aid}"] = {"status": r.status_code, "size": len(r.content), "url": url}
            if r.status_code == 200 and len(r.content) > 5000:
                body = r.text
                if "Подождите" not in body:
                    tm = re.search(r'<title[^>]*>([^<]+)', body, re.I)
                    results[f"c_{aid}_real"] = {"title": tm.group(1) if tm else None, "size": len(body)}
        except Exception as e:
            log.info("C %s ERR: %s", aid, str(e)[:80])
        time.sleep(2)

# ============================================================
# SAVE
# ============================================================
out = Path(__file__).parent / "diag_v60_out.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("=== DONE %.0fs. %d keys ===", time.monotonic() - t0, len(results))
