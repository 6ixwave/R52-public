"""WB v3: warm-up cookies + mobile + wbbasket + wbx-content + разные impersonation."""
import json, sys, time, re, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

WB_IDS = ["199298871", "314337853"]
results = []

from curl_cffi import requests as cr

def probe(label, url, session=None, impersonate="chrome", expect_json=False, params=None, headers=None, verify=True):
    print(f"\n{'='*60}")
    print(f"[{label}] {url}")
    if params:
        print(f"  params: {params}")
    t0 = time.monotonic()
    try:
        kwargs = dict(impersonate=impersonate, timeout=15, verify=verify)
        if params:
            kwargs["params"] = params
        if headers:
            kwargs["headers"] = headers
        if session:
            r = session.get(url, **kwargs)
        else:
            r = cr.get(url, **kwargs)
        elapsed = time.monotonic() - t0
        ct = r.headers.get("Content-Type", "?")
        print(f"  Status: {r.status_code}, {len(r.content)} bytes, {elapsed:.1f}s, CT={ct}")
        if r.status_code == 200:
            if expect_json:
                try:
                    data = r.json()
                    keys = list(data.keys())[:15] if isinstance(data, dict) else f"list[{len(data)}]"
                    print(f"  JSON keys: {keys}")
                    return data
                except Exception as e:
                    print(f"  JSON parse fail: {e}")
            text = r.text
            ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.DOTALL)
            if ld:
                print(f"  JSON-LD: {len(ld)} blocks")
                for i, block in enumerate(ld[:3]):
                    try:
                        obj = json.loads(block.strip())
                        if isinstance(obj, dict):
                            print(f"    [{i}] @type={obj.get('@type','?')} name={str(obj.get('name',''))[:60]} price={obj.get('offers','')}")
                        elif isinstance(obj, list):
                            for j, item in enumerate(obj[:3]):
                                print(f"    [{i}.{j}] @type={item.get('@type','?')} name={str(item.get('name',''))[:60]}")
                    except:
                        pass
            og = re.findall(r'<meta[^>]*(?:property|name)=["\'](?:og:|twitter:)(?:title|price:amount|price:currency|image)["\'][^>]*content=["\']([^"\']*)["\']', text)
            if og:
                print(f"  OG/Twitter: {og[:6]}")
            return text
        else:
            body = r.text[:300] if r.text else "(empty)"
            print(f"  Body: {body}")
            return None
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"  ERROR ({elapsed:.1f}s): {type(e).__name__}: {str(e)[:200]}")
        return None

# === 1. WARM-UP: main page to get cookies ===
print("\n" + "#"*60)
print("# WARM-UP: main page")
print("#"*60)
session = cr.Session(impersonate="chrome")
r_main = probe("WB main page", "https://www.wildberries.ru/", session=session)
if r_main:
    cookies = dict(session.cookies)
    print(f"  Cookies after warm-up: {list(cookies.keys())}")
    for k, v in cookies.items():
        print(f"    {k} = {v[:40]}...")
else:
    print("  Warm-up failed, trying without session")
    session = None

time.sleep(2)

# === 2. card.wb.ru with session ===
print("\n" + "#"*60)
print("# card.wb.ru with session")
print("#"*60)
for wid in WB_IDS:
    data = probe(f"card v2 session {wid}", "https://card.wb.ru/cards/v2/detail",
                 session=session, impersonate="chrome", expect_json=True, params={"nm": wid})
    if data:
        prods = data.get("data", {}).get("products", [])
        if prods:
            p = prods[0]
            print(f"  OK: {p.get('name','?')[:50]} brand={p.get('brand','?')}")
            results.append({"id": wid, "method": "card_v2_session", "status": "OK"})
        else:
            results.append({"id": wid, "method": "card_v2_session", "status": "NO_PRODUCTS"})
    else:
        results.append({"id": wid, "method": "card_v2_session", "status": "FAIL"})
    time.sleep(1)

# === 3. Mobile site ===
print("\n" + "#"*60)
print("# Mobile site m.wildberries.ru")
print("#"*60)
for wid in WB_IDS:
    url = f"https://m.wildberries.ru/catalog/{wid}/detail.aspx"
    text = probe(f"WB mobile {wid}", url, session=session, impersonate="chrome")
    if text:
        results.append({"id": wid, "method": "mobile", "status": "OK", "len": len(text)})
    else:
        results.append({"id": wid, "method": "mobile", "status": "FAIL"})
    time.sleep(1)

# === 4. wbx-content-v2 (product content/images) ===
print("\n" + "#"*60)
print("# wbx-content-v2.wbstatic.net")
print("#"*60)
for wid in WB_IDS:
    # Pattern: https://wbx-content-v2.wbstatic.net/ru/{id}/
    url = f"https://wbx-content-v2.wbstatic.net/ru/{wid}/"
    text = probe(f"wbx-content {wid}", url, impersonate="chrome")
    if text:
        results.append({"id": wid, "method": "wbx_content", "status": "OK", "len": len(text)})
    else:
        results.append({"id": wid, "method": "wbx_content", "status": "FAIL"})
    time.sleep(1)

# === 5. static-basket with CORRECT domain (wbbasket.ru) ===
print("\n" + "#"*60)
print("# static-basket wbbasket.ru (correct domain)")
print("#"*60)
for basket_num in (1, 2, 3, 4):
    for wid in WB_IDS[:1]:  # just first ID to test
        url = f"https://static-basket-{basket_num:02d}.wbbasket.ru/v2/images/s100/{wid}"
        r = probe(f"basket{basket_num:02d} img {wid}", url, impersonate="chrome")
        if r is not None:
            results.append({"id": wid, "method": f"static_basket{basket_num:02d}", "status": "OK"})
            break
    time.sleep(0.5)

# === 6. Different impersonation levels ===
print("\n" + "#"*60)
print("# Different impersonation: safari, edge, firefox")
print("#"*60)
for imp in ("safari", "edge", "firefox"):
    wid = WB_IDS[0]
    data = probe(f"card v2 {imp} {wid}", "https://card.wb.ru/cards/v2/detail",
                 impersonate=imp, expect_json=True, params={"nm": wid})
    if data:
        results.append({"id": wid, "method": f"card_{imp}", "status": "OK"})
    else:
        results.append({"id": wid, "method": f"card_{imp}", "status": "FAIL"})
    time.sleep(1)

# === 7. catalog.wb.ru v1 catalog (not search) ===
print("\n" + "#"*60)
print("# catalog.wb.ru/v1/catalog")
print("#"*60)
data = probe("catalog v1", "https://catalog.wb.ru/v1/catalog",
             session=session, impersonate="chrome", expect_json=True,
             params={"root": "1", "nm": WB_IDS[0]})
if data:
    results.append({"id": "catalog", "method": "catalog_v1", "status": "OK"})
else:
    results.append({"id": "catalog", "method": "catalog_v1", "status": "FAIL"})

# === 8. wb.ru API (newer) ===
print("\n" + "#"*60)
print("# wb.ru / api endpoints")
print("#"*60)
for wid in WB_IDS:
    url = f"https://www.wildberries.ru/api/catalog/{wid}/detail"
    data = probe(f"wb api detail {wid}", url, session=session, impersonate="chrome", expect_json=True)
    if data:
        results.append({"id": wid, "method": "wb_api_detail", "status": "OK"})
    else:
        results.append({"id": wid, "method": "wb_api_detail", "status": "FAIL"})
    time.sleep(1)

# === 9. Try with Accept header forcing JSON ===
print("\n" + "#"*60)
print("# Force Accept: application/json")
print("#"*60)
for wid in WB_IDS:
    data = probe(f"card v2 JSON accept {wid}", "https://card.wb.ru/cards/v2/detail",
                 session=session, impersonate="chrome", expect_json=True,
                 params={"nm": wid},
                 headers={"Accept": "application/json"})
    if data:
        results.append({"id": wid, "method": "card_json_accept", "status": "OK"})
    else:
        results.append({"id": wid, "method": "card_json_accept", "status": "FAIL"})
    time.sleep(1)

# === Summary ===
print(f"\n{'='*60}")
print("WB v3 SUMMARY:")
for r in results:
    print(f"  {r}")

out = Path(__file__).parent / "diag_wb_v3_results.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {out}")
