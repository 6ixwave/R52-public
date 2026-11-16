"""v112b: Lightweight E2E — 1 URL per platform, minimal RAM."""
import json, re, sys, time, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

report = {}

# 1. FARPOST — 1 SEO URL
print("=== FARPOST ===")
try:
    from curl_cffi import requests as cffi_req
    s = cffi_req.Session(impersonate="chrome124")
    url = "https://www.farpost.ru/vladivostok/food/seafood/nerka-pbg-2025-2026-god-raznye-razmery-kamchatka-dostavka-66422428.html"
    r = s.get(url, timeout=(10,20), headers={"Accept":"text/html","Accept-Language":"ru-RU"})
    e = {"http": r.status_code, "bytes": len(r.content)}
    if r.status_code == 200:
        tm = re.search(r'<title>(.*?)</title>', r.text, re.DOTALL)
        e["title"] = tm.group(1).strip()[:100] if tm else None
        bp = re.search(r'data-bulletin-price="([^"]*)"', r.text)
        e["price"] = bp.group(1) if bp else None
        ld = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', r.text, re.DOTALL)
        for lb in ld:
            try:
                d = json.loads(lb.strip())
                if d.get("@type")=="Product":
                    e["jsonld_name"] = d.get("name","")[:80]
                    off = d.get("offers",{})
                    e["jsonld_price"] = str(off.get("price","")) if isinstance(off,dict) else str(off)[:50]
                    break
            except: pass
        e["gallery"] = len(re.findall(r'data-image-info', r.text))
    report["farpost"] = e
    print(f"  {e}")
except Exception as ex:
    report["farpost"] = {"exc": str(ex)}
    print(f"  EXC: {ex}")

# 2. OZON — mobile + __rr
print("\n=== OZON ===")
try:
    from curl_cffi import requests as cffi_req
    s = cffi_req.Session(impersonate="chrome124")
    h = {"Accept":"text/html","Accept-Language":"ru-RU","User-Agent":"Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36"}
    s.get("https://www.ozon.ru/", timeout=(10,20), headers=h)
    time.sleep(4)
    url = "https://www.ozon.ru/product/5699176004/?__rr=1"
    r = s.get(url, timeout=(10,25), headers=h)
    e = {"http": r.status_code, "bytes": len(r.content), "final": r.url[:80]}
    if r.status_code == 200:
        tm = re.search(r'<title>(.*?)</title>', r.text, re.DOTALL)
        e["title"] = tm.group(1).strip()[:100] if tm else None
        prices = re.findall(r'(\d[\d\s]{2,12})\s*(?:₽|руб)', r.text[:60000])
        e["prices"] = prices[:5]
        e["state"] = bool(re.search(r'window\.__STATE__', r.text))
    elif r.status_code == 403:
        e["altcha"] = bool(re.search(r'altcha|challenge', r.text[:3000], re.I))
    report["ozon"] = e
    print(f"  {e}")
except Exception as ex:
    report["ozon"] = {"exc": str(ex)}
    print(f"  EXC: {ex}")

# 3. AVITO — PoW + 1 ad
print("\n=== AVITO ===")
try:
    from curl_cffi import requests as cffi_req
    s = cffi_req.Session(impersonate="chrome124")
    h = {"Accept":"text/html","Accept-Language":"ru-RU"}
    r = s.get("https://www.avito.ru/", timeout=(10,20), headers=h)
    e = {"home_http": r.status_code, "cookies": list(s.cookies.keys())}
    # Extract PoW from scripts
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', r.text, re.DOTALL)
    pow_s = ""
    for sc in scripts:
        if 'pow' in sc.lower() or 'nonce' in sc.lower() or 'challenge' in sc.lower():
            pow_s = sc[:800]
            break
    e["pow_script"] = pow_s[:400]
    # Try category
    time.sleep(4)
    r2 = s.get("https://www.avito.ru/vladivostok/elektronika/telefony", timeout=(10,25), headers=h)
    e["cat_http"] = r2.status_code
    e["cat_bytes"] = len(r2.content)
    if r2.status_code == 200:
        ids = list(dict.fromkeys(re.findall(r'/ad/(\d{8,})', r2.text)))[:3]
        e["ids"] = ids
    report["avito"] = e
    print(f"  {e}")
except Exception as ex:
    report["avito"] = {"exc": str(ex)}
    print(f"  EXC: {ex}")

# 4. WB — card.json + webapi
print("\n=== WB ===")
try:
    from curl_cffi import requests as cffi_req
    s = cffi_req.Session(impersonate="chrome124")
    nm = 199298871
    vol, part = nm//100000, nm//1000
    r = s.get(f"https://basket-13.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/card.json", timeout=12, headers={"Accept":"application/json"})
    e = {"card_http": r.status_code, "card_bytes": len(r.content)}
    if r.status_code == 200:
        d = r.json()
        e["name"] = d.get("imt_name","")[:80]
        e["brand"] = d.get("selling",{}).get("brand_name","")
        e["options"] = len(d.get("options",[]))
        e["media"] = len(d.get("media",{}).get("photo",{}).get("im",[])) if isinstance(d.get("media"),dict) else 0
    # Price attempt
    r2 = s.get(f"https://card.wb.ru/cards/v2/detail?nm={nm}", timeout=10, headers={"Accept":"application/json","Referer":f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"})
    e["price_http"] = r2.status_code
    e["price_bytes"] = len(r2.content)
    if r2.status_code == 200:
        try:
            pd = r2.json()
            if isinstance(pd, list) and pd:
                txt = json.dumps(pd[0])
                e["price_vals"] = re.findall(r'"(?:price|basePrice|salePrice|priceU)"\s*:\s*"?([\d.]+)"?', txt)[:5]
        except: pass
    report["wb"] = e
    print(f"  {e}")
except Exception as ex:
    report["wb"] = {"exc": str(ex)}
    print(f"  EXC: {ex}")

# Save
out = Path(__file__).parent / "diag_v112b_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nReport: {out}")
print("DONE")
