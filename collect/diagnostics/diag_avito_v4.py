"""Avito v4: 90s. Full 429 body analysis + mobile API + session-based approach."""
import json, time, logging, re, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("avito4")

from curl_cffi import requests as cr

HH = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
      "Referer": "https://www.avito.ru/",
      "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
      "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
      "Upgrade-Insecure-Requests": "1"}

results = {}
t0 = time.monotonic()

def g(label, url, hdrs=HH, to=12):
    el = time.monotonic() - t0
    if el > 85:
        log.info("BUDGET at %s", label)
        return 0, None
    try:
        r = cr.get(url, headers=hdrs, impersonate="chrome", timeout=to)
        log.info("%-28s %d %7dB", label, r.status_code, len(r.content))
        return r.status_code, r
    except Exception as e:
        log.info("%-28s ERR %s", label, str(e)[:70])
        return 0, None

# === 1) FULL 429 BODY (35KB — the biggest one) ===
log.info("=== 1) FULL 429 BODY ===")
u = "https://www.avito.ru/ekaterinburg/transport"
st, r = g("cat_429", u)
if st in (200, 429, 439) and r and len(r.content) > 5000:
    body = r.text
    key = "cat_429_full"
    results[key] = {"status": st, "size": len(body)}
    
    # Save full body for offline analysis
    Path(__file__).parent / "diag_avito_429_body.html".write_text(body, encoding="utf-8")
    
    # Broader ID extraction
    ids = re.findall(r'/ad/(\d{6,})', body)
    if not ids:
        ids = re.findall(r'"(?:adId|ad_id|id)"\s*:\s*"?(\d{8,12})"?', body)
    if not ids:
        ids = re.findall(r'(?:data-ad-id|data-id|data-card-id)=["\'](\d+)', body)
    if not ids:
        # Any 9-10 digit numbers
        ids = re.findall(r'\b(\d{9,10})\b', body)
    results[key]["ids"] = list(dict.fromkeys(ids))[:20]
    log.info("  *** IDs: %s", results[key]["ids"][:5])
    
    # JSON blocks (any script with JSON-like content)
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', body, re.DOTALL)
    json_blocks = []
    for i, s in enumerate(scripts):
        s2 = s.strip()
        if len(s2) > 100 and (s2.startswith('{') or s2.startswith('[') or '__' in s2[:50]):
            json_blocks.append({"idx": i, "len": len(s2), "head": s2[:150]})
    results[key]["json_scripts"] = json_blocks[:10]
    
    # Look for __NEXT_DATA__, __PRELOADED_STATE__, window.__
    for pat_name, pat in [
        ("next_data", r'__NEXT_DATA__\s*=\s*(\{.+?\})\s*;?\s*</script>'),
        ("preloaded", r'__PRELOADED_STATE__\s*=\s*(\{.+?\})'),
        ("initial", r'__INITIAL_STATE__\s*=\s*(\{.+?\})'),
        ("nuxt", r'window\.__NUXT__\s*=\s*(\{.+?\})'),
    ]:
        m = re.search(pat, body, re.DOTALL)
        if m:
            results[key]["state_type"] = pat_name
            log.info("  *** STATE: %s (%d chars)", pat_name, len(m.group(1)))
            try:
                sd = json.loads(m.group(1))
                results[key]["state_keys"] = list(sd.keys())[:20]
            except: pass
            break
    
    # Title
    tm = re.search(r'<title[^>]*>([^<]+)', body, re.I)
    if tm:
        results[key]["title"] = tm.group(1)
    
    # Meta tags
    for prop in ["og:title", "og:description", "og:image", "og:url"]:
        m = re.search(rf'property=["\']{prop}["\'][^>]*content=["\']([^"\']+)', body, re.I)
        if m:
            results[key][prop] = m.group(1)[:200]
    
    # Links to ads (href="/...ad/...")
    ad_links = re.findall(r'href=["\'](/[^"']*?/\d{6,})["\']', body)
    if not ad_links:
        ad_links = re.findall(r'href=["\']([^"']*?/ad/\d+)["\']', body)
    results[key]["ad_links"] = list(dict.fromkeys(ad_links))[:15]
    log.info("  *** ad_links: %s", results[key]["ad_links"][:5])
    
    # First 1000 chars
    results[key]["head"] = body[:1000]

# === 2) MOBILE: m.avito.ru ===
log.info("=== 2) MOBILE ===")
mob_urls = [
    ("m_cat", "https://m.avito.ru/moscow/transport"),
    ("m_search", "https://m.avito.ru/moscow/search?q=iphone"),
    ("m_cat2", "https://m.avito.ru/spb/elektronika"),
]
for lbl, url in mob_urls:
    st, r = g(f"mob_{lbl}", url)
    if st == 200 and r and len(r.content) > 3000:
        body = r.text
        results[f"mob_{lbl}"] = {"status": st, "size": len(body)}
        ids = re.findall(r'/ad/(\d{6,})', body)
        if not ids:
            ids = re.findall(r'"id"\s*:\s*"?(\d{8,12})"?', body)
        results[f"mob_{lbl}"]["ids"] = list(dict.fromkeys(ids))[:15]
        log.info("  *** mob %s: %d IDs", lbl, len(results[f"mob_{lbl}"]["ids"]))
        if ids:
            break
    time.sleep(2)

# === 3) API: Avito search API ===
log.info("=== 3) API ===")
api_urls = [
    ("api_search", "https://www.avito.ru/api/search?category=transport&city=moscow&page=1&limit=10"),
    ("api_cards", "https://www.avito.ru/api/cards?city=moscow&category=transport"),
    ("api_v2", "https://www.avito.ru/api/v2/search?query=телефон&city=moscow"),
]
for lbl, url in api_urls:
    st, r = g(f"api_{lbl}", url, HH, 10)
    if st == 200 and r and len(r.content) > 100:
        try:
            d = r.json()
            results[f"api_{lbl}"] = d
            log.info("  *** API %s OK: %s", lbl, json.dumps(d, ensure_ascii=False)[:200])
        except:
            results[f"api_{lbl}"] = r.text[:500]
    time.sleep(1.5)

# === 4) FETCH REAL AD (if any ID found) ===
all_ids = results.get("cat_429_full", {}).get("ids", [])
for k in results:
    if k.startswith("mob_") and results[k].get("ids"):
        all_ids = results[k]["ids"]
        break

if all_ids:
    log.info("=== 4) FETCH AD %s ===", all_ids[0])
    aid = all_ids[0]
    for lbl, url in [("short", f"https://www.avito.ru/ad/{aid}"),
                     ("mob", f"https://m.avito.ru/ad/{aid}")]:
        st, r = g(f"ad_{lbl}", url)
        if st == 200 and r and len(r.content) > 3000:
            html = r.text
            results[f"ad_{aid}"] = {"url": url, "size": len(html)}
            # Quick extract
            tm = re.search(r'<title[^>]*>([^<]+)', html, re.I)
            if tm: results[f"ad_{aid}"]["title"] = tm.group(1)
            prices = re.findall(r'(\d{3,9}[.,]?\d{0,2})\s*(?:₽|руб)', html)
            if prices: results[f"ad_{aid}"]["prices"] = prices[:5]
            imgs = re.findall(r'https?://[^"\'\s]*avito\.st[^"\'\s]*', html)
            if imgs: results[f"ad_{aid}"]["images"] = list(dict.fromkeys(imgs))[:8]
            # JSON-LD
            for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.I|re.DOTALL):
                try:
                    results[f"ad_{aid}"]["jsonld"] = json.loads(m.group(1))
                    break
                except: pass
            break
        time.sleep(2)

# === SAVE ===
out = Path(__file__).parent / "diag_avito_v4_out.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
log.info("=== DONE %.0fs. %d keys ===", time.monotonic()-t0, len(results))
