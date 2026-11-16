"""Быстрая диагностика v3: fetch 4 URL, дамп HTML + индикаторы."""
import json, re, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "diag_html"
OUT.mkdir(exist_ok=True)

USE_CFFI = False
try:
    from curl_cffi import requests as cffi_req
    USE_CFFI = True
    print("[diag] Using curl_cffi", flush=True)
except ImportError:
    import requests as cffi_req
    print("[diag] curl_cffi NOT found, using stdlib requests", flush=True)

H = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

def _get(url, timeout=30):
    if USE_CFFI:
        return cffi_req.get(url, impersonate="chrome", headers=H, timeout=timeout)
    return cffi_req.get(url, headers=H, timeout=timeout)

def fetch(url, name, timeout=30):
    t0 = time.monotonic()
    try:
        r = _get(url, timeout)
        el = time.monotonic() - t0
        html = r.text
        ct = r.headers.get("Content-Type", "")
        print(f"\n{'='*60}", flush=True)
        print(f"[{name}] {url}", flush=True)
        print(f"  status={r.status_code} ct={ct} len={len(html)} t={el:.1f}s", flush=True)
        print(f"  head200: {html[:200]!r}", flush=True)

        ind = {
            "og_title": bool(re.search(r'og:title', html, re.I)),
            "json_ld": bool(re.search(r'application/ld\+json', html, re.I)),
            "h1": bool(re.search(r'<h1', html, re.I)),
            "title_tag": bool(re.search(r'<title', html, re.I)),
            "state_var": bool(re.search(r'__PRELOADED_STATE__|__INITIAL_STATE__|__STATE__|window\._state', html, re.I)),
            "captcha": bool(re.search(r'captcha|altcha|challenge|verify', html, re.I)),
            "404": bool(re.search(r'не существует|not found|404|Такой страницы', html, re.I)),
            "price_hint": bool(re.search(r'data-price|data-bulletin-price|"price"|₽|руб', html, re.I)),
            "imgs": len(re.findall(r'<img', html, re.I)),
            "scripts": len(re.findall(r'<script', html, re.I)),
        }
        print(f"  ind: {json.dumps(ind, ensure_ascii=False)}", flush=True)

        (OUT / f"{name}.html").write_text(html[:300000], encoding="utf-8")
        print(f"  saved {name}.html ({len(html[:300000])}ch)", flush=True)

        # Source-specific
        if name == "OZON":
            widgets = re.findall(r'data-widget="([^"]+)"', html)
            print(f"  widgets({len(widgets)}): {widgets[:15]}", flush=True)
            og = re.findall(r'<meta[^>]*property="og:([^"]+)"[^>]*content="([^"]{0,80})"', html, re.I)
            print(f"  og: {og[:8]}", flush=True)
            for pat in [r'"price"\s*:\s*"?(\d+)', r'(\d[\d\s]{3,})\s*₽']:
                pm = re.findall(pat, html)
                if pm: print(f"  price[{pat[:25]}]: {pm[:5]}", flush=True)

        if name == "AVITO":
            og = re.findall(r'<meta[^>]*property="og:([^"]+)"[^>]*content="([^"]{0,80})"', html, re.I)
            print(f"  og: {og[:8]}", flush=True)
            m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*', html)
            print(f"  PRELOADED_STATE: {'yes@'+str(m.start()) if m else 'no'}", flush=True)
            for pat in [r'"price"\s*:\s*"?(\d+)', r'data-ad-price=["\']?(\d+)']:
                pm = re.findall(pat, html)
                if pm: print(f"  price[{pat[:25]}]: {pm[:5]}", flush=True)

        if name == "FARPOST":
            raw = r.content[:300]
            print(f"  raw_head: {raw[:150]!r}", flush=True)
            m = re.search(r'charset=([^\s";]+)', html[:1000], re.I)
            print(f"  charset: {m.group(1) if m else 'none'}", flush=True)
            og = re.findall(r'<meta[^>]*property="og:([^"]+)"[^>]*content="([^"]{0,80})"', html, re.I)
            print(f"  og: {og[:8]}", flush=True)
            m2 = re.search(r'data-bulletin-price="(\d+)"', html)
            print(f"  bulletin-price: {m2.group(1) if m2 else 'N/A'}", flush=True)

    except Exception as e:
        print(f"\n[{name}] ERR: {type(e).__name__}: {e}", flush=True)
        import traceback; traceback.print_exc()

def fetch_wb(nm_id_str):
    nm_id = int(nm_id_str)
    vol = nm_id // 100000
    part = nm_id // 1000
    print(f"\n{'='*60}", flush=True)
    print(f"[WB] nm_id={nm_id} vol={vol} part={part}", flush=True)

    # 1. card.json
    for hn in [42, 41, 40, 39, 38, 37, 36, 35]:
        url = f"https://basket-{hn:02d}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
        try:
            r = _get(url, 15)
            print(f"  card basket-{hn:02d}: {r.status_code} len={len(r.text)}", flush=True)
            if r.status_code == 200 and len(r.text) > 10:
                data = r.json()
                card = data[0] if isinstance(data, list) else data
                selling = card.get("selling", {})
                print(f"  selling keys: {list(selling.keys()) if isinstance(selling, dict) else type(selling)}", flush=True)
                print(f"  selling: {json.dumps(selling, ensure_ascii=False, default=str)[:500]}", flush=True)
                nums = {k:v for k,v in card.items() if isinstance(v,(int,float)) and v>0}
                print(f"  top nums: {nums}", flush=True)
                sizes = card.get("sizes", [])
                if sizes and isinstance(sizes[0], dict):
                    s0 = sizes[0]
                    print(f"  sizes[0] keys: {list(s0.keys())[:20]}", flush=True)
                    for pk in ("price","priceU","priceD","oldPrice","totalPrice","salePriceU","salePriceD"):
                        if pk in s0: print(f"  sizes[0].{pk}: {s0[pk]}", flush=True)
                    szp = s0.get("price")
                    if isinstance(szp, dict):
                        print(f"  sizes[0].price dict: {json.dumps(szp, ensure_ascii=False)[:300]}", flush=True)
                (OUT/"WB_card.json").write_text(json.dumps(data, ensure_ascii=False, indent=2)[:80000])
                break
        except Exception as e:
            print(f"  basket-{hn:02d} err: {e}", flush=True)

    # 2. price API
    time.sleep(1)
    url = f"https://card.wb.ru/cards/v2/detail?nm={nm_id}"
    try:
        r = _get(url, 15)
        print(f"\n  price_api: {r.status_code} len={len(r.text)}", flush=True)
        if r.status_code == 200:
            data = r.json()
            prods = data.get("data",{}).get("products",[])
            if prods:
                p = prods[0]
                print(f"  prod keys: {list(p.keys())[:35]}", flush=True)
                for pk in ("name","brand","priceU","priceD","salePriceU","salePriceD","oldPriceU","oldPriceD","totalPriceU","totalPriceD"):
                    if pk in p: print(f"  prod.{pk}: {p[pk]}", flush=True)
                (OUT/"WB_price.json").write_text(json.dumps(data, ensure_ascii=False, indent=2)[:50000])
            else:
                print(f"  NO products! data keys: {list(data.get('data',{}).keys())}", flush=True)
    except Exception as e:
        print(f"  price_api err: {e}", flush=True)

print("DIAG v3 START", flush=True)
t0 = time.monotonic()
fetch_wb("1097975275")
time.sleep(1)
fetch("https://www.ozon.ru/product/562267/", "OZON")
time.sleep(2)
fetch("https://www.avito.ru/ad/7494308705", "AVITO")
time.sleep(2)
fetch("https://www.farpost.ru/vladivostok/1154321987.html", "FARPOST")
print(f"\nDIAG v3 DONE {time.monotonic()-t0:.1f}s", flush=True)
