"""diag_v118.py — Focused: WB media patterns, WB price, Farpost full-path."""
import json, sys, time, re, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag")
sys.path.insert(0, r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12")

from curl_cffi import requests as cffi_req

BASE = r"C:\Users\aleks\Downloads\R52-2.12\R52-2.12"

# ===== WB =====
NM_ID = 199298871
IMT_ID = 158033827
VOL = NM_ID // 100000
PART = NM_ID // 1000

print("=" * 60)
print(f"WB MEDIA PATTERNS (nm={NM_ID}, imt={IMT_ID}, vol={VOL}, part={PART})")
print("=" * 60)

# 1. card.json — check for media fields
CARD_URL = f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/info/ru/card.json"
print(f"\n[1] card.json: {CARD_URL}")
card = None
try:
    r = cffi_req.get(CARD_URL, impersonate="chrome", timeout=15)
    print(f"    HTTP {r.status_code}, {len(r.content)}B")
    if r.status_code == 200:
        card = r.json()
        print(f"    Keys: {sorted(card.keys())}")
        for key in sorted(card.keys()):
            if any(x in key.lower() for x in ("media", "photo", "image", "img", "video", "gallery")):
                val = card[key]
                print(f"    '{key}': {json.dumps(val, ensure_ascii=False)[:400]}")
        opts = card.get("options", [])
        print(f"    options: {len(opts)} items")
        for o in opts[:5]:
            print(f"      {o.get('name','?')} = {str(o.get('value','?'))[:40]}")
        with open(f"{BASE}\\wb_card_v118.json", "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, indent=2)
        print("    -> wb_card_v118.json saved")
except Exception as e:
    print(f"    ERROR: {e}")

time.sleep(2)

# 2. Image URL patterns
print(f"\n[2] Image URL patterns:")
img_patterns = [
    # wbbasket.ru (old CDN)
    f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/images/S800/S800.jpg",
    f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/images/S120/S120.jpg",
    f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/images/thumbnail/thumbnail.jpg",
    f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/images/big/big.jpg",
    f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/images/1000x0/1000x0.jpg",
    # wbxstatic.ru (newer CDN)
    f"https://ir-1.wbxstatic.ru/big/{NM_ID}.jpg",
    f"https://ir-1.wbxstatic.ru/1000x0/{NM_ID}.jpg",
    f"https://ir-1.wbxstatic.ru/120x120/{NM_ID}.jpg",
    f"https://ir-1.wbxstatic.ru/120x0/{NM_ID}.jpg",
    f"https://ir-2.wbxstatic.ru/big/{NM_ID}.jpg",
    f"https://ir-2.wbxstatic.ru/1000x0/{NM_ID}.jpg",
    f"https://ir-3.wbxstatic.ru/big/{NM_ID}.jpg",
    # imt_id based
    f"https://ir-1.wbxstatic.ru/big/{IMT_ID}.jpg",
    f"https://ir-1.wbxstatic.ru/1000x0/{IMT_ID}.jpg",
]

for url in img_patterns:
    try:
        r = cffi_req.get(url, impersonate="chrome", timeout=10)
        ct = r.headers.get("content-type", "")
        size = len(r.content)
        ok = "OK" if r.status_code == 200 and size > 500 and "image" in ct else "xx"
        print(f"  [{ok}] {r.status_code} {size:>8}B {ct[:35]:35} ...{url[-55:]}")
    except Exception as e:
        print(f"  [ERR] {str(e)[:50]:50} ...{url[-55:]}")
    time.sleep(0.5)

# 3. WB Price
print(f"\n{'=' * 60}")
print(f"WB PRICE ENDPOINTS (nm={NM_ID})")
print("=" * 60)

price_endpoints = [
    ("card.v2", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("card.v4", f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("card.v5", f"https://card.wb.ru/cards/v5/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={NM_ID}"),
    ("web.api", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={NM_ID}"),
    ("basket.info", f"https://basket-13.wbbasket.ru/vol{VOL}/part{PART}/{NM_ID}/info/ru/card.json"),
]

hdrs = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}

for label, url in price_endpoints:
    try:
        r = cffi_req.get(url, impersonate="chrome", headers=hdrs, timeout=12)
        print(f"\n  [{label}] HTTP {r.status_code}, {len(r.content)}B")
        if r.status_code == 200:
            data = r.json()
            text = json.dumps(data, ensure_ascii=False)
            # Find price-like fields
            pm = re.findall(r'"(?:price|basic|product|old|crossed|bankCard|wallet|card|total)"\s*:\s*(\d+)', text)
            if pm:
                print(f"    price fields: {pm[:12]}")
            prods = data.get("data", {}).get("items") or data.get("products") or []
            if prods:
                p = prods[0]
                print(f"    name={p.get('name','?')[:50]}")
                sizes = p.get("sizes", [])
                if sizes:
                    s0 = sizes[0]
                    print(f"    sizes[0]: price={s0.get('price')}, qty={s0.get('qty')}")
            if "prices" in data:
                print(f"    data.prices = {data['prices']}")
            if "price" in data:
                print(f"    data.price = {data['price']}")
            # Save first good one
            out = f"{BASE}\\wb_price_{label}.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"    -> {out}")
        elif r.status_code in (403, 429):
            print(f"    blocked, body[:100]={r.text[:100]}")
    except Exception as e:
        print(f"  [{label}] ERROR: {e}")
    time.sleep(1.5)

# ===== FARPOST =====
print(f"\n{'=' * 60}")
print("FARPOST: Full-path + numeric path")
print("=" * 60)

FP_FULL = "https://www.farpost.ru/ni/krasnoyarsk/zapchasti/kontraktnyj-dvigatel-1zz-fe-cena-s-navesnym-129606872.html"
FP_NUM = "https://www.farpost.ru/bulletin/129606872"

s = cffi_req.Session(impersonate="chrome")

# Warmup
print("\n[Warmup] farpost.ru/")
try:
    r0 = s.get("https://www.farpost.ru/", timeout=15)
    print(f"  HTTP {r0.status_code}, {len(r0.content)}B, cookies={list(s.cookies.keys())}")
except Exception as e:
    print(f"  error: {e}")
time.sleep(2)

# Full path
print(f"\n[Full] {FP_FULL}")
try:
    r = s.get(FP_FULL, timeout=20)
    print(f"  HTTP {r.status_code}, {len(r.content)}B")
    if r.status_code == 200:
        html = r.text
        m = re.search(r'data-bulletin-price=["\'](\d+)["\']', html)
        print(f"  data-bulletin-price: {m.group(1) if m else 'NOT FOUND'}")
        
        ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        print(f"  JSON-LD blocks: {len(ld)}")
        for i, b in enumerate(ld):
            try:
                d = json.loads(b.strip())
                t = d.get("@type","?") if isinstance(d,dict) else "list"
                print(f"    [{i}] {t}")
                if isinstance(d,dict) and t=="Product":
                    print(f"        name={d.get('name','?')[:50]}")
                    off = d.get("offers",{})
                    if isinstance(off,dict):
                        print(f"        price={off.get('price')} {off.get('priceCurrency')}")
                    elif isinstance(off,list):
                        for o in off[:2]:
                            print(f"        offer: price={o.get('price')} avail={o.get('availability')}")
                    img = d.get("image","")
                    if isinstance(img,str): print(f"        image={img[:70]}")
                    elif isinstance(img,list): print(f"        images: {len(img)}")
            except: pass
        
        photos = re.findall(r'data-image-info="(\{.*?\})"', html)
        print(f"  data-image-info: {len(photos)} photos")
        if photos:
            try:
                p0 = json.loads(photos[0].replace("&quot;",'"'))
                print(f"    first msrc: {p0.get('msrc','?')[:70]}")
            except: pass
        
        lb = re.findall(r'baza\.lightbox\.init\(\s*(\[[\s\S]*?\])\s*\)', html)
        print(f"  lightbox.init: {len(lb)} blocks")
        
        m = re.search(r'data-field="subject"[^>]*>(.*?)</span>', html, re.DOTALL)
        title = re.sub(r'<[^>]+>','',m.group(1)).strip() if m else "?"
        print(f"  Title: {title[:60]}")
        
        with open(f"{BASE}\\farpost_v118.html","w",encoding="utf-8") as f:
            f.write(html)
        print(f"  -> farpost_v118.html ({len(html)} chars)")
except Exception as e:
    print(f"  ERROR: {e}")

time.sleep(2)

# Numeric path
print(f"\n[Numeric] {FP_NUM}")
try:
    r = s.get(FP_NUM, timeout=15, allow_redirects=False)
    print(f"  HTTP {r.status_code}, {len(r.content)}B")
    if r.status_code in (301,302,307,308):
        print(f"  Location: {r.headers.get('Location','?')}")
    elif r.status_code == 200:
        m = re.search(r'<title[^>]*>([^<]+)</title>', r.text, re.I)
        print(f"  Title: {m.group(1)[:60] if m else '?'}")
        print(f"  Size: {len(r.content)} (real page if >20000)")
except Exception as e:
    print(f"  ERROR: {e}")

print(f"\n{'=' * 60}")
print("DIAG V118 DONE")
print("=" * 60)
