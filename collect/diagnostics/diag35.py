#!/usr/bin/env python3
"""diag35: WB basket CORRECT part + FP feed URL + Avito offer. Target <80s."""
import json, re, time, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

OUT = Path(__file__).parent / "d35_results.json"
results = {}
t0 = time.time()

def log(tag, msg):
    el = time.time() - t0
    print(f"[{el:.1f}s] {tag}: {msg}", flush=True)

def safe_get(url, headers=None, timeout=10):
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
    if headers:
        h.update(headers)
    req = Request(url, headers=h)
    try:
        resp = urlopen(req, timeout=timeout)
        return resp.status, resp.read()
    except HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return -1, str(e).encode()

def decode_smart(raw):
    for enc in ["utf-8", "windows-1251", "cp1251"]:
        try:
            t = raw.decode(enc)
            if '\ufffd' not in t[:200]:
                return t, enc
        except:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-forced"

# ============ WB: CORRECT part number ============
def wb_basket_correct():
    """BUG FIX: part = NM // 100 = 10979752 (not 1097975!)"""
    nm = 1097975275
    vol = nm // 100000  # 10979
    part = nm // 100    # 10979752  <-- CORRECT!
    
    print(f"  [WB] nm={nm} vol={vol} part={part}", flush=True)
    
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }
    
    # Try basket hosts 01-20 with correct part
    for i in range(1, 21):
        for prefix in ["basket", "static-basket"]:
            host = f"{prefix}-{i:02d}.wbbasket.ru"
            u = f"https://{host}/vol{vol}/part{part}/{nm}.json"
            code, data = safe_get(u, h, timeout=6)
            if code == 200 and len(data) > 50:
                try:
                    j = json.loads(data)
                    name = j.get("name", "")
                    price_u = j.get("salePriceU", j.get("priceU", None))
                    price = price_u / 100 if price_u else None
                    brand = j.get("brand", "")
                    detail = f"host={host} name={name!r:.60} price={price} brand={brand} len={len(data)}"
                    log(f"WB:fix", f"HIT {host}: {name[:50]} price={price}")
                    return True, detail, j
                except Exception as e:
                    log(f"WB:fix", f"{host}: JSON error {e} len={len(data)}")
                    return True, f"{host}: non-json len={len(data)}", {"raw": data[:500].decode('utf-8','replace')}
            elif code not in (404, -1):
                log(f"WB:fix", f"{host}: HTTP={code} len={len(data)}")
    
    # Also try wbx-content with correct structure
    for ver in ["v1", "v2", "v3"]:
        u = f"https://wbx-content-{ver}.wbstatic.net/card/v2/{nm}.json"
        code, data = safe_get(u, h, timeout=8)
        if code == 200 and len(data) > 50:
            try:
                j = json.loads(data)
                log(f"WB:fix", f"HIT wbx-{ver}: len={len(data)}")
                return True, f"wbx-{ver}: len={len(data)}", j
            except:
                pass
        u2 = f"https://wbx-content-{ver}.wbstatic.net/card/{nm}.json"
        code, data = safe_get(u2, h, timeout=8)
        if code == 200 and len(data) > 50:
            try:
                j = json.loads(data)
                log(f"WB:fix", f"HIT wbx-{ver} v1: len={len(data)}")
                return True, f"wbx-{ver}-v1: len={len(data)}", j
            except:
                pass
    
    return False, f"vol={vol} part={part} tried=40+6", None

# ============ WB: second NM to verify formula ============
def wb_basket_nm2():
    """Test with a different NM to confirm the vol/part formula."""
    # Use a known WB product (small number for easy verification)
    nm = 123456789  # test
    vol = nm // 100000
    part = nm // 100
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
    
    for i in [1, 5, 10, 15, 20]:
        host = f"basket-{i:02d}.wbbasket.ru"
        u = f"https://{host}/vol{vol}/part{part}/{nm}.json"
        code, data = safe_get(u, h, timeout=5)
        log(f"WB:nm2", f"{host}: HTTP={code} len={len(data)}")
        if code == 200 and len(data) > 50:
            return True, f"{host}: len={len(data)}", data[:500].decode('utf-8','replace')
    
    # Also test our main NM with a few more hosts
    nm2 = 1097975275
    vol2 = nm2 // 100000
    part2 = nm2 // 100
    for i in [21, 25, 30, 35, 40, 45, 50, 55, 60]:
        host = f"basket-{i:02d}.wbbasket.ru"
        u = f"https://{host}/vol{vol2}/part{part2}/{nm2}.json"
        code, data = safe_get(u, h, timeout=5)
        if code == 200 and len(data) > 50:
            try:
                j = json.loads(data)
                log(f"WB:nm2", f"HIT {host}: {j.get('name','')[:40]}")
                return True, f"{host}: {j.get('name','')[:50]}", j
            except:
                pass
        elif code not in (404, -1):
            log(f"WB:nm2", f"{host}: HTTP={code} len={len(data)}")
    
    return False, "nm2_sweep_done", None

# ============ FP: feed URL from JSON blob + item structure ============
def fp_feed_url():
    """The JSON blob #2 had: offset, exactFeedCount, directory, city, site, url, reqsig.
    Extract the feed URL and hit it directly."""
    url = "https://m.farpost.ru/vladivostok/auto/misc"
    h = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    code, raw = safe_get(url, h, timeout=15)
    if code != 200:
        return False, f"HTTP={code}", None
    html, enc = decode_smart(raw)
    
    # Extract all JSON blobs
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
    json_blobs = []
    for s in scripts:
        s_stripped = s.strip()
        if s_stripped.startswith('{') or s_stripped.startswith('['):
            try:
                obj = json.loads(s_stripped)
                json_blobs.append(obj)
            except:
                pass
    
    # Find the feed config blob (has "url" and "offset")
    feed_config = None
    for blob in json_blobs:
        if isinstance(blob, dict) and "url" in blob and ("offset" in blob or "exactFeedCount" in blob):
            feed_config = blob
            break
    
    # Also look for the item data in the page
    # The 50 data-bull-id elements - let's get their full HTML context
    bull_elements = []
    for m in re.finditer(r'data-bull(?:etin)?-?id="(\d+)"', html):
        start = max(0, m.start() - 300)
        end = min(len(html), m.end() + 500)
        bull_elements.append({
            "id": m.group(1),
            "context": html[start:end]
        })
    
    # Get first 3 in detail
    sample = bull_elements[:3]
    
    # Look for the actual item container structure
    # Between the first and second data-bull-id
    if len(bull_elements) >= 2:
        first_pos = re.search(r'data-bull(?:etin)?-?id="(\d+)"', html)
        second_pos = re.search(r'data-bull(?:etin)?-?id="(\d+)"', html[first_pos.end():])
        if second_pos:
            item_html = html[first_pos.start():first_pos.end() + second_pos.start()]
            log(f"FP:feed", f"item_block_len={len(item_html)}")
    
    detail = f"blobs={len(json_blobs)} feed_cfg={bool(feed_config)} bulls={len(bull_elements)}"
    
    extract = {
        "feed_config": feed_config,
        "json_blobs_summary": [
            {k: v for k, v in b.items() if k in ("url","offset","exactFeedCount","directory","city","site","reqsig","viewdirType")}
            if isinstance(b, dict) else "list"
            for b in json_blobs[:5]
        ],
        "bull_samples": sample,
        "bull_count": len(bull_elements),
    }
    
    ok = feed_config is not None or len(bull_elements) > 10
    return ok, detail, extract

# ============ FP: parse items from www category ============
def fp_www_items():
    """www.farpost.ru category (166KB) - parse actual item structure."""
    url = "https://www.farpost.ru/vladivostok/auto/misc"
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    code, raw = safe_get(url, h, timeout=15)
    if code != 200:
        return False, f"HTTP={code}", None
    html, enc = decode_smart(raw)
    
    # Find all data-bull-id elements with surrounding context
    bull_matches = list(re.finditer(r'data-bull(?:etin)?-?id="(\d+)"', html))
    
    # For each bull, extract: title (nearby <a> or <h3>), price (nearby span with price class)
    items = []
    for i, m in enumerate(bull_matches[:30]):
        bid = m.group(1)
        # Look in a window around this element
        start = max(0, m.start() - 500)
        end = min(len(html), m.end() + 800)
        window = html[start:end]
        
        # Title: look for <a> with text or <h3>/<h4>
        title_m = re.search(r'<(?:a|h[1-6]|span)[^>]*class="[^"]*(?:title|name|subj)[^"]*"[^>]*>([^<]{5,150})<', window, re.I)
        if not title_m:
            title_m = re.search(r'<a[^>]*href="[^"]*?/\d{6,12}[^"]*"[^>]*>([^<]{5,150})<', window)
        
        # Price: look for price class element
        price_m = re.search(r'class="[^"]*(?:price|cost)[^"]*"[^>]*>\s*([\d\s.,]+)', window, re.I)
        if not price_m:
            # Broader: any number near "руб" or "₽"
            price_m = re.search(r'(\d{2,10})\s*(?:\u20bd|руб|₽|р\.)', window)
        
        # Image
        img_m = re.search(r'(?:src|data-src)="([^"]*farpost[^"]*|[^"]*bulletin[^"]*)"', window, re.I)
        
        items.append({
            "id": bid,
            "title": title_m.group(1).strip()[:80] if title_m else None,
            "price": price_m.group(1).strip() if price_m else None,
            "img": img_m.group(1)[:80] if img_m else None,
        })
    
    # Count how many have price and title
    with_price = sum(1 for i in items if i["price"])
    with_title = sum(1 for i in items if i["title"])
    
    detail = f"enc={enc} len={len(html)} bulls={len(bull_matches)} items={len(items)} wp={with_price} wt={with_title}"
    
    extract = {
        "items": items[:20],
        "stats": {"total_bulls": len(bull_matches), "with_price": with_price, "with_title": with_title},
        "first_item_raw": html[max(0,bull_matches[0].start()-200):bull_matches[0].end()+400] if bull_matches else None,
    }
    
    ok = with_price > 5
    return ok, detail, extract

# ============ AVITO: specific offer + mobile API ============
def avito_offer():
    """Try specific offer pages and mobile API endpoints."""
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    
    # 1. Try a specific well-known offer pattern
    offer_urls = [
        "https://www.avito.ru/vladivostok/smartfony-ASgBAgICAkSSA8YQ?cid=182",
        "https://m.avito.ru/vladivostok/smartfony",
    ]
    
    results_data = []
    for u in offer_urls:
        code, data = safe_get(u, h, timeout=12)
        html = data.decode("utf-8", errors="replace") if code == 200 else ""
        
        # Look for offers in various formats
        offers_data = re.findall(r'"offers"\s*:\s*\[', html)
        prices = re.findall(r'"price"\s*:\s*(\d+)', html)
        titles = re.findall(r'"title"\s*:\s*"([^"]{5,100})"', html)
        # data-qa attributes
        data_qa = re.findall(r'data-qa="([^"]*)"', html)
        
        # Look for JSON in scripts
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
        json_data = []
        for s in scripts:
            if len(s) > 200 and ('"offers"' in s or '"price"' in s or '__INITIAL' in s or '__STATE' in s):
                json_data.append(s[:300])
        
        detail = f"HTTP={code} len={len(html)} offers={len(offers_data)} prices={len(prices)} titles={len(titles)} dqa={len(data_qa)}"
        log(f"AV:offer", f"{u.split('/')[2]}: {detail}")
        results_data.append({"url": u, "http": code, "len": len(html), "prices": prices[:10], "titles": titles[:5], "json": json_data[:2]})
        
        if code == 200 and (len(prices) > 3 or len(offers_data) > 0):
            return True, f"{u.split('/')[2]}: {detail}", results_data
    
    # 2. Try Avito mobile API
    api_urls = [
        "https://m.avito.ru/api/offers?region=2&category=182&limit=10",
        "https://www.avito.ru/api/v1/offers?region=2&category=182",
        "https://m.avito.ru/vladivostok/smartfony?_format=json",
    ]
    for u in api_urls:
        code, data = safe_get(u, {**h, "Accept": "application/json"}, timeout=10)
        text = data.decode("utf-8", errors="replace")[:500]
        log(f"AV:api", f"{u.split('/')[2]}: HTTP={code} len={len(data)} preview={text[:100]!r}")
        if code == 200 and len(data) > 200:
            try:
                j = json.loads(data)
                return True, f"API {u}: {list(j.keys())[:5]}", j
            except:
                pass
    
    ok = any(r.get("http") == 200 and r.get("len", 0) > 5000 for r in results_data)
    return ok, f"items={len(results_data)}", results_data

# ============ RUN ============
if __name__ == "__main__":
    tests = [
        ("WB:basket_fix", wb_basket_correct),
        ("WB:nm2", wb_basket_nm2),
        ("FP:feed", fp_feed_url),
        ("FP:www_items", fp_www_items),
        ("AV:offer", avito_offer),
    ]
    
    for tag, fn in tests:
        try:
            ok, detail, extra = fn()
            results[tag] = {"ok": ok, "detail": detail, "s": round(time.time() - t0, 1)}
            if extra:
                results[tag]["data"] = extra
            log(tag, f"{'PASS' if ok else 'FAIL'}: {detail}")
        except Exception as e:
            import traceback
            results[tag] = {"ok": False, "detail": f"EXC: {e}", "s": round(time.time() - t0, 1)}
            log(tag, f"EXC: {e}\n{traceback.format_exc()}")
    
    results["_meta"] = {"total_s": round(time.time() - t0, 1), "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{time.time()-t0:.1f}s] DONE -> {OUT.name}", flush=True)
