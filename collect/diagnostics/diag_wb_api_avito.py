"""Диагностика: WB API (card.wb.ru) + Avito (проверка старых HTML)."""
import json, os, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
OUT = Path(__file__).parent

from curl_cffi import requests as cffi_req

print("DIAG WB API + AVITO")
print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

# ═══════════════════════════════════════════
# PART 1: WB API — card.wb.ru
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print("WB API: card.wb.ru/cards/v2/detail")
print(f"{'='*60}")

wb_ids = ["199298871", "188497640", "12736071", "189316415", "7610591"]
wb_ok = False

for nm in wb_ids:
    url = f"https://card.wb.ru/cards/v2/detail?nm={nm}"
    print(f"\n--- WB API nm={nm} ---")
    print(f"  URL: {url}")
    try:
        r = cffi_req.get(url, impersonate="chrome", timeout=20,
                         headers={"Accept": "application/json"})
        print(f"  HTTP: {r.status_code}, len: {len(r.text)}")
        if r.status_code == 200:
            data = r.json()
            products = data.get("data", {}).get("products", [])
            if products:
                p = products[0]
                name = p.get("name", "?")
                brand = p.get("brand", "?")
                sale_price = p.get("salePriceU", 0)  # kopecks
                price_u = p.get("priceU", 0)
                imgs = p.get("img", [])
                cats = p.get("categories", [])
                cat_str = " > ".join(str(c.get("name","")) for c in cats) if cats else "?"
                print(f"  NAME: {name[:80]}")
                print(f"  BRAND: {brand}")
                print(f"  SALE_PRICE: {sale_price} kop = {sale_price//100} RUB")
                print(f"  PRICE_U: {price_u} kop = {price_u//100} RUB")
                print(f"  IMAGES: {len(imgs)}")
                if imgs:
                    print(f"    sample: {imgs[0][:80]}")
                print(f"  CATEGORIES: {cat_str[:100]}")
                print(f"  ID: {p.get('id')}")
                print(f"  SIZE: {p.get('size', '?')}")
                print(f"  RATING: {p.get('rating', '?')}")
                # Save full JSON
                jf = OUT / f"diag_wb_api_{nm}.json"
                jf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  Saved: {jf.name} ({jf.stat().st_size} B)")
                wb_ok = True
            else:
                print(f"  WARNING: no products in response")
                print(f"  Keys: {list(data.keys())}")
        else:
            print(f"  Body[:200]: {r.text[:200]}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
    time.sleep(1)

# ═══════════════════════════════════════════
# PART 2: Avito — check existing HTML files
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print("AVITO: checking existing diag HTML files")
print(f"{'='*60}")

avito_files = [
    OUT / "diag_v48_avito_iphone-1154321987.html",
    OUT / "diag_v49_avito_desktop_ad_1154321987.html",
    OUT / "diag_v49_avito_mobile_ad_iphone-1154321987.html",
]

for af in avito_files:
    if not af.exists():
        print(f"\n  {af.name}: NOT FOUND")
        continue
    html = af.read_text(encoding="utf-8", errors="replace")
    print(f"\n--- {af.name} ({len(html)} B) ---")
    # Title
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.I)
    print(f"  <title>: {(m.group(1).strip()[:80] if m else '?')}")
    # H1
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.I)
    print(f"  <h1>: {(m.group(1).strip()[:80] if m else '?')}")
    # og:title
    m = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
    print(f"  og:title: {(m.group(1)[:80] if m else '?')}")
    # JSON-LD
    blocks = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.I)
    print(f"  JSON-LD blocks: {len(blocks)}")
    for i, b in enumerate(blocks[:3]):
        try:
            d = json.loads(b.strip())
            t = d.get("@type", "?")
            name = str(d.get("name", ""))[:60]
            price = ""
            off = d.get("offers")
            if isinstance(off, dict):
                price = f" price={off.get('price','?')} {off.get('priceCurrency','')}"
            elif isinstance(off, list) and off:
                price = f" price={off[0].get('price','?')}"
            print(f"    [{i}] type={t} name={name}{price}")
        except Exception:
            print(f"    [{i}] parse-error, len={len(b)}")
    # Prices
    prices = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
    print(f"  Price mentions: {len(prices)}, sample: {prices[:5]}")
    # avito.st images
    imgs = re.findall(r'(?:src|data-src)=["\']([^"\']*avito\.st[^"\']*)["\']', html, re.I)
    print(f"  avito.st imgs: {len(imgs)}")
    if imgs:
        print(f"    sample: {imgs[0][:80]}")
    # State
    for pat_name, pat in [
        ("__PRELOADED_STATE__", r'window\.__PRELOADED_STATE__\s*=\s*(\{.+?\})'),
        ("__INITIAL_STATE__", r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\})'),
    ]:
        m = re.search(pat, html, re.DOTALL)
        if m:
            print(f"  STATE[{pat_name}]: len={len(m.group(1))}")
            break
    # CAPTCHA check
    cap = any(x in html[:5000].lower() for x in
              ["captcha", "recaptcha", "smartcaptcha", "turnstile", "cf-challenge"])
    print(f"  CAPTCHA: {cap}")
    # 404 check
    is_404 = "404" in (m.group(1) if m else "") or "не существует" in html[:3000].lower()
    print(f"  404-like: {is_404}")

# ═══════════════════════════════════════════
# PART 3: Avito — try real IDs from category
# ═══════════════════════════════════════════
print(f"\n{'='*60}")
print("AVITO: trying category page for real ad IDs")
print(f"{'='*60}")

# Try the noutbuki category page
cat_url = "https://www.avito.ru/all/noutbuki"
try:
    r = cffi_req.get(cat_url, impersonate="chrome", timeout=25,
                     headers={"Accept-Language": "ru-RU,ru;q=0.9"})
    html = r.text
    print(f"\n  Category: {cat_url}")
    print(f"  HTTP: {r.status_code}, len: {len(html)}")
    hf = OUT / "diag_avito_cat_noutbuki.html"
    hf.write_text(html, encoding="utf-8")
    print(f"  Saved: {hf.name} ({hf.stat().st_size} B)")
    
    # Find ad links: /ad/ or /<city>/<cat>/<id>
    ad_ids = set()
    # Pattern: avito.ru/.../1234567890
    for m in re.finditer(r'avito\.ru/[^"\'\s]*?/(\d{9,12})', html):
        ad_ids.add(m.group(1))
    # Pattern: data-ad-id or similar
    for m in re.finditer(r'data-ad-id=["\'](\d+)["\']', html, re.I):
        ad_ids.add(m.group(1))
    # Pattern: /ad/1234567890
    for m in re.finditer(r'/ad/(\d{9,12})', html):
        ad_ids.add(m.group(1))
    
    print(f"  Found ad IDs: {len(ad_ids)}")
    if ad_ids:
        sorted_ids = sorted(ad_ids)[:10]
        print(f"  Sample: {sorted_ids}")
        
        # Try first 3
        for aid in sorted_ids[:3]:
            ad_url = f"https://www.avito.ru/ad/{aid}"
            print(f"\n  --- Avito ad: {ad_url} ---")
            try:
                r2 = cffi_req.get(ad_url, impersonate="chrome", timeout=25,
                                  headers={"Accept-Language": "ru-RU,ru;q=0.9"})
                h2 = r2.text
                print(f"    HTTP: {r2.status_code}, len: {len(h2)}")
                m_t = re.search(r'<title[^>]*>(.*?)</title>', h2, re.DOTALL | re.I)
                print(f"    <title>: {(m_t.group(1).strip()[:80] if m_t else '?')}")
                cap2 = any(x in h2[:5000].lower() for x in
                           ["captcha", "recaptcha", "smartcaptcha", "turnstile"])
                is_404_2 = "404" in (m_t.group(1) if m_t else "") or "не существует" in h2[:3000].lower()
                print(f"    CAPTCHA: {cap2}, 404: {is_404_2}")
                hf2 = OUT / f"diag_avito_ad_{aid}.html"
                hf2.write_text(h2, encoding="utf-8")
                print(f"    Saved: {hf2.name} ({hf2.stat().st_size} B)")
                if r2.status_code == 200 and not cap2 and not is_404_2 and len(h2) > 5000:
                    print(f"    >>> SUCCESS! Real ad page")
            except Exception as e:
                print(f"    ERROR: {type(e).__name__}: {e}")
            time.sleep(2)
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

print(f"\n{'='*60}")
print(f"RESULT: WB_API={'OK' if wb_ok else 'FAIL'}")
print("DONE")
