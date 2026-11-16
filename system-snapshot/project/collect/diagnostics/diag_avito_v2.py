"""Avito v2: найти РЕАЛЬНЫЕ ID через категорию, затем fetch карточки."""
import json, time, logging, re, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("avito2")

from curl_cffi import requests as cr

HH = {"Accept": "text/html,*/*", "Accept-Language": "ru-RU,ru;q=0.9",
      "Referer": "https://www.avito.ru/"}

results = {}
t0 = time.monotonic()

# === STEP 1: Find real ad IDs ===
log.info("=== 1) FIND IDs ===")
cat_urls = [
    ("moscow_transport", "https://www.avito.ru/moscow/transport"),
    ("spb_electronics", "https://www.avito.ru/spb/elektronika"),
    ("moscow_phones", "https://www.avito.ru/moscow/telefony"),
]
found_ids = []
for lbl, url in cat_urls:
    try:
        r = cr.get(url, headers=HH, impersonate="chrome", timeout=12)
        log.info("%-20s %d %dB", lbl, r.status_code, len(r.content))
        if r.status_code == 200:
            # Extract ad IDs: /ad/123456789 or data-ad-id="123"
            ids = re.findall(r'["\']?/ad/(\d{6,})["\']?', r.text)
            if not ids:
                ids = re.findall(r'data-ad-id=["\'](\d{6,})["\']', r.text)
            if not ids:
                ids = re.findall(r'"adId"\s*:\s*["\']?(\d{6,})', r.text)
            if not ids:
                # broader: any 9-10 digit number near "ad"
                ids = re.findall(r'(\d{9,10})', r.text)
            found_ids = list(dict.fromkeys(ids))[:15]
            log.info("  *** %d IDs found: %s", len(found_ids), found_ids[:5])
            if found_ids:
                results["category"] = lbl
                results["category_url"] = url
                results["category_size"] = len(r.content)
                # Save first 2000 chars for debug
                results["category_snippet"] = r.text[:2000]
                break
        elif r.status_code in (301, 302):
            loc = r.headers.get("Location", "")
            log.info("  redirect -> %s", loc[:100])
            if "avito" in loc:
                r2 = cr.get(loc, headers=HH, impersonate="chrome", timeout=12)
                log.info("  follow: %d %dB", r2.status_code, len(r2.content))
                if r2.status_code == 200:
                    ids = re.findall(r'["\']?/ad/(\d{6,})["\']?', r2.text)
                    found_ids = list(dict.fromkeys(ids))[:15]
                    if found_ids:
                        results["category"] = lbl + "_follow"
                        break
        time.sleep(2)
    except Exception as e:
        log.info("%-20s ERR %s", lbl, str(e)[:80])

if not found_ids:
    log.warning("No IDs found from categories. Trying direct ad URL pattern...")
    # Fallback: try a known popular ad pattern
    found_ids = ["4601234567", "4598765432", "4587654321"]

log.info("Target IDs: %s", found_ids[:5])

# === STEP 2: Fetch first 2-3 ads ===
log.info("=== 2) FETCH CARDS ===")
for idx, aid in enumerate(found_ids[:3]):
    if time.monotonic() - t0 > 120:
        log.info("BUDGET exceeded"); break
    card_urls = [
        ("short", f"https://www.avito.ru/ad/{aid}"),
        ("full", f"https://www.avito.ru/moscow/transport/{aid}"),
    ]
    for lbl, url in card_urls:
        try:
            r = cr.get(url, headers=HH, impersonate="chrome", timeout=12)
            log.info("ad[%d] %-8s %d %dB %s", idx, lbl, r.status_code, len(r.content), url[:50])
            if r.status_code == 200 and len(r.content) > 2000:
                html = r.text
                key = f"ad_{idx}_{aid}"
                results[key] = {"url": url, "size": len(html)}
                
                # CAPTCHA check
                lower = html[:8000].lower()
                captcha = any(m in lower for m in ["smartcaptcha","captcha","recaptcha","cf-challenge","turnstile"])
                if captcha:
                    results[key]["captcha"] = True
                    log.info("  *** CAPTCHA on ad %s", aid)
                    continue
                results[key]["captcha"] = False
                
                # JSON-LD
                for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.I|re.DOTALL):
                    try:
                        ld = json.loads(m.group(1))
                        results[key]["jsonld"] = ld
                        log.info("  *** JSON-LD: %s", json.dumps(ld, ensure_ascii=False)[:250])
                    except: pass
                
                # State
                for pat in [r'window\.__PRELOADED_STATE__\s*=\s*(\{.+?\})\s*;',
                            r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*;']:
                    m = re.search(pat, html, re.DOTALL)
                    if m:
                        try:
                            st = json.loads(m.group(1))
                            results[key]["state_keys"] = list(st.keys())[:15]
                            ad = st.get("ad") or st.get("data",{}).get("ad") or {}
                            if ad:
                                results[key]["ad_name"] = ad.get("name","")
                                results[key]["ad_price"] = ad.get("price") or ad.get("priceValue")
                                results[key]["ad_desc"] = (ad.get("description") or "")[:300]
                                results[key]["ad_imgs"] = (ad.get("images") or ad.get("photos") or [])[:5]
                            log.info("  *** State: %d keys, ad_name=%s", len(st), ad.get("name","?")[:40])
                        except Exception as e:
                            log.info("  *** State parse: %s", str(e)[:60])
                        break
                
                # og: meta
                for prop in ["og:title","og:description","og:image","og:price:amount"]:
                    m = re.search(rf'property=["\']{prop}["\'][^>]*content=["\']([^"\']+)', html, re.I)
                    if m:
                        results[key][prop] = m.group(1)[:200]
                
                # Prices in HTML
                prices = re.findall(r'(\d{3,8}[.,]?\d{0,2})\s*(?:₽|руб)', html)
                if prices:
                    results[key]["prices_html"] = prices[:8]
                
                # Images
                imgs = re.findall(r'https?://[^"\'\s]*(?:avatars\.mmedia\.avito\.st|l\d+\.avito\.st)[^"\'\s]*', html)
                if imgs:
                    results[key]["images"] = list(dict.fromkeys(imgs))[:10]
                
                break  # first success for this ad
            elif r.status_code in (301, 302):
                loc = r.headers.get("Location", "")
                log.info("  redirect -> %s", loc[:80])
                results[key] = {"url": url, "redirect": loc}
            time.sleep(2)
        except Exception as e:
            log.info("ad[%d] %-8s ERR %s", idx, lbl, str(e)[:80])
        time.sleep(2)

# === SAVE ===
out = Path(__file__).parent / "diag_avito_v2_out.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("=== DONE %.0fs. %d result keys. Saved: %s ===", time.monotonic()-t0, len(results), out)
