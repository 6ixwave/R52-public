"""diag_v119.py — Verify new WB/Farpost patterns from v118 findings.

Key findings from v118:
- WB images: ir-2.wbxstatic.ru/1000x0/{nm_id}[_N].jpg (from HTML)
- WB price: data-price in HTML + card.json data.sizes[].price
- Farpost: data-full on <img>, data-bulletin-price in span, h1 > span[data-field=subject]
- Farpost CDN: static.baza.farpost.ru/v/{id}_{N}_bulletin.jpg
"""
import json, sys, time, re, socket, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag")
sys.path.insert(0, r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12")

from curl_cffi import requests as cffi_req

BASE = r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12"
NM_ID = 199298871
IMT_ID = 158033827
VOL = NM_ID // 100000
PART = NM_ID // 1000

# ============================================================
# PART 1: DNS check for wbxstatic.ru
# ============================================================
print("=" * 60)
print("PART 1: DNS RESOLUTION — wbxstatic.ru")
print("=" * 60)

for host in ["ir-1.wbxstatic.ru", "ir-2.wbxstatic.ru", "ir-3.wbxstatic.ru",
             "basket-13.wbbasket.ru", "static.baza.farpost.ru"]:
    try:
        ip = socket.getaddrinfo(host, 443, socket.AF_INET)[0][4][0]
        print(f"  [OK] {host:35} -> {ip}")
    except Exception as e:
        print(f"  [XX] {host:35} -> {str(e)[:60]}")

# ============================================================
# PART 2: WB image URLs (new pattern from HTML)
# ============================================================
print(f"\n{'=' * 60}")
print(f"PART 2: WB IMAGE URL VERIFICATION (nm={NM_ID})")
print("=" * 60)

# From HTML: ir-2.wbxstatic.ru/1000x0/{nm_id}[_N].jpg
wb_img_urls = [
    # Primary (first photo)
    f"https://ir-2.wbxstatic.ru/1000x0/{NM_ID}.jpg",
    # Additional photos
    f"https://ir-2.wbxstatic.ru/1000x0/{NM_ID}_2.jpg",
    f"https://ir-2.wbxstatic.ru/1000x0/{NM_ID}_3.jpg",
    # Thumbnail
    f"https://ir-2.wbxstatic.ru/120x120/{NM_ID}.jpg",
    # Video
    f"https://ir-2.wbxstatic.ru/video/{NM_ID}.mp4",
    # Try ir-1 as well
    f"https://ir-1.wbxstatic.ru/1000x0/{NM_ID}.jpg",
    # imt_id based
    f"https://ir-2.wbxstatic.ru/1000x0/{IMT_ID}.jpg",
]

for url in wb_img_urls:
    try:
        r = cffi_req.get(url, impersonate="chrome", timeout=12)
        ct = r.headers.get("content-type", "")
        size = len(r.content)
        ok = "OK " if r.status_code == 200 and size > 1000 else "xx "
        kind = "IMG" if "image" in ct else ("VID" if "video" in ct else "???")
        print(f"  [{ok}]{kind} {r.status_code} {size:>8}B {ct[:30]:30} {url.split('://')[1][:55]}")
    except Exception as e:
        print(f"  [ERR] {str(e)[:50]:50} {url.split('://')[1][:55]}")
    time.sleep(0.3)

# ============================================================
# PART 3: WB HTML page (price + media in one fetch)
# ============================================================
print(f"\n{'=' * 60}")
print(f"PART 3: WB HTML PAGE (nm={NM_ID})")
print("=" * 60)

WB_HTML_URL = f"https://www.wildberries.ru/catalog/{NM_ID}/detail.aspx"
print(f"  URL: {WB_HTML_URL}")

wb_html = ""
try:
    r = cffi_req.get(WB_HTML_URL, impersonate="chrome",
                     headers={"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9"},
                     timeout=25)
    print(f"  HTTP {r.status_code}, {len(r.content)}B")
    if r.status_code == 200:
        wb_html = r.text
        # Price
        m = re.search(r'data-price=["\'](\d+)["\']', wb_html)
        print(f"  data-price: {m.group(1) if m else 'NOT FOUND'}")
        m = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', wb_html)
        print(f"  og:price:amount: {m.group(1) if m else 'NOT FOUND'}")
        m = re.search(r'data-qty=["\'](\d+)["\']', wb_html)
        print(f"  data-qty: {m.group(1) if m else 'NOT FOUND'}")
        
        # Images
        imgs = re.findall(r'(?:src|data-full)=["\'](https://[^"\']*wbxstatic\.ru[^"\']+)["\']', wb_html)
        imgs = list(dict.fromkeys(imgs))  # dedupe
        print(f"  wbxstatic images: {len(imgs)}")
        for u in imgs[:8]:
            print(f"    {u}")
        
        # Video
        vids = re.findall(r'(?:src|data-src)=["\'](https://[^"\']*wbxstatic\.ru[^"\']*\.(?:mp4|webm))["\']', wb_html)
        print(f"  wbxstatic videos: {len(vids)}")
        for u in vids[:3]:
            print(f"    {u}")
        
        # Title
        m = re.search(r'<h1[^>]*>(.*?)</h1>', wb_html, re.DOTALL)
        title = re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else "?"
        print(f"  Title: {title[:60]}")
        
        # Save
        with open(f"{BASE}\\wb_html_v119.html", "w", encoding="utf-8") as f:
            f.write(wb_html)
        print(f"  -> wb_html_v119.html ({len(wb_html)} chars)")
except Exception as e:
    print(f"  ERROR: {e}")

# ============================================================
# PART 4: WB card.json price extraction
# ============================================================
print(f"\n{'=' * 60}")
print(f"PART 4: WB CARD.JSON PRICE (nm={NM_ID})")
print("=" * 60)

CARD_URL = f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/info/ru/card.json"
try:
    r = cffi_req.get(CARD_URL, impersonate="chrome", timeout=15)
    print(f"  HTTP {r.status_code}, {len(r.content)}B")
    if r.status_code == 200:
        card = r.json()
        data = card.get("data", {})
        sizes = data.get("sizes", [])
        print(f"  data.sizes: {len(sizes)} entries")
        for s in sizes:
            price = s.get("price", {})
            qty = s.get("qty", "?")
            name = s.get("name", "?")
            print(f"    [{name}] qty={qty} price={price}")
        selling = card.get("selling", {})
        print(f"  brand: {selling.get('brand_name', '?')}")
        print(f"  vendor_code: {selling.get('vendor_code', '?')}")
        print(f"  media: {card.get('media', {})}")
except Exception as e:
    print(f"  ERROR: {e}")

# ============================================================
# PART 5: Farpost — verify new patterns on live page
# ============================================================
print(f"\n{'=' * 60}")
print("PART 5: FARPOST LIVE VERIFICATION")
print("=" * 60)

FP_URL = "https://www.farpost.ru/ni/krasnoyarsk/zapchasti/kontraktnyj-dvigatel-1zz-fe-cena-s-navesnym-129606872.html"
s = cffi_req.Session(impersonate="chrome")

# Warmup
try:
    r0 = s.get("https://www.farpost.ru/", timeout=15)
    print(f"  [Warmup] HTTP {r0.status_code}, cookies={list(s.cookies.keys())}")
except Exception as e:
    print(f"  [Warmup] error: {e}")
time.sleep(2)

try:
    r = s.get(FP_URL, timeout=25)
    print(f"  [Page] HTTP {r.status_code}, {len(r.content)}B")
    if r.status_code == 200:
        html = r.text
        
        # Title (new pattern: h1 > span[data-field=subject])
        m = re.search(r'<h1[^>]*class="[^"]*subject[^"]*"[^>]*>\s*<span[^>]*data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
        if not m:
            m = re.search(r'data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
        title = re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else "?"
        print(f"  Title: {title[:70]}")
        
        # Price (data-bulletin-price in span)
        m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        price = m.group(1) if m else "NOT FOUND"
        print(f"  data-bulletin-price: {price}")
        
        # Old price
        m = re.search(r'viewbull-summary-price__old[^>]*>([\d\s\u00a0]+)', html)
        old_price = m.group(1).strip() if m else "N/A"
        print(f"  old price: {old_price}")
        
        # New image pattern: <img ... data-full="...bulletin.jpg">
        imgs = re.findall(r'<img[^>]*data-full=["\'](https://[^"\']*farpost[^"\']+)["\']', html)
        if not imgs:
            # Fallback: src with _default
            imgs = re.findall(r'<img[^>]*src=["\'](https://[^"\']*farpost[^"\']*_(?:default|bulletin)[^"\']*)["\']', html)
        imgs = list(dict.fromkeys(imgs))
        print(f"  Photos (data-full): {len(imgs)}")
        for u in imgs[:8]:
            print(f"    {u}")
        
        # Video
        vids = re.findall(r'<video[^>]*>.*?<source[^>]*src=["\']([^"\']+)["\']', html, re.DOTALL)
        if not vids:
            vids = re.findall(r'(?:src|data-src)=["\']([^"\']*\.(?:mp4|webm))["\']', html)
        print(f"  Videos: {len(vids)}")
        for u in vids[:3]:
            print(f"    {u}")
        
        # Specs
        specs = re.findall(r'<div\s+class="label"[^>]*>([^<]+)</div>\s*<div\s+class="value"[^>]*>\s*<span[^>]*>(.*?)</span>', html, re.DOTALL)
        print(f"  Specs: {len(specs)}")
        for k, v in specs[:5]:
            v = re.sub(r'<[^>]+>', '', v).strip()
            print(f"    {k.strip()}: {v[:40]}")
        
        # Seller
        m = re.search(r'class="userNick[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>', html, re.DOTALL)
        seller = re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else "?"
        print(f"  Seller: {seller}")
        
        # Availability
        m = re.search(r'data-field="goodPresentState"[^>]*>\s*([^<]+)', html)
        avail = m.group(1).strip() if m else "?"
        print(f"  Availability: {avail}")
        
        # Save
        with open(f"{BASE}\\farpost_v119.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  -> farpost_v119.html ({len(html)} chars)")
        
except Exception as e:
    print(f"  ERROR: {e}")

# ============================================================
# PART 6: Farpost CDN image verification
# ============================================================
print(f"\n{'=' * 60}")
print("PART 6: FARPOST CDN IMAGE VERIFICATION")
print("=" * 60)

fp_img_urls = [
    "https://static.baza.farpost.ru/v/129606872_1_bulletin.jpg",
    "https://static.baza.farpost.ru/v/129606872_1_default.jpg",
    "https://static.baza.farpost.ru/v/129606872_2_bulletin.jpg",
    "https://static.baza.farpost.ru/v/129606872_video.mp4",
]

for url in fp_img_urls:
    try:
        r = cffi_req.get(url, impersonate="chrome", timeout=12)
        ct = r.headers.get("content-type", "")
        size = len(r.content)
        ok = "OK " if r.status_code == 200 and size > 500 else "xx "
        kind = "IMG" if "image" in ct else ("VID" if "video" in ct else "???")
        print(f"  [{ok}]{kind} {r.status_code} {size:>8}B {ct[:30]:30} {url.split('farpost.ru/')[1][:45]}")
    except Exception as e:
        print(f"  [ERR] {str(e)[:50]:50} {url.split('farpost.ru/')[1][:45]}")
    time.sleep(0.3)

print(f"\n{'=' * 60}")
print("DIAG V119 DONE")
print("=" * 60)
