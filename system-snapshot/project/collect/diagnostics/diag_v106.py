"""Diagnostic v106: WB price endpoints + Farpost fetch + card.json selling field."""
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v106")

report = {}

# ============================================================
# WB PRICE
# ============================================================
print("=" * 60)
print("WB PRICE DIAGNOSTIC")
print("=" * 60)

nm_id = 199298871
wb = None
try:
    from r52.collectors.wb import WBCollector, JSON_HEADERS, HTML_HEADERS
    wb = WBCollector(data_dir=None, use_proxies=False)
except Exception as e:
    print(f"WB import/init error: {e}")
    report["wb_error"] = str(e)

if wb:
    # 1. card.json — full selling field
    print("\n--- 1. card.json selling/price fields ---")
    card_data = wb._fetch_card_json(nm_id)
    if card_data:
        selling = card_data.get("selling", {})
        report["card_selling_keys"] = list(selling.keys()) if isinstance(selling, dict) else str(type(selling))
        print(f"  selling type: {type(selling).__name__}")
        if isinstance(selling, dict):
            print(f"  selling keys: {list(selling.keys())}")
            print(f"  selling (truncated): {json.dumps(selling, ensure_ascii=False)[:1500]}")
        # Any price-like top-level keys
        for k in card_data:
            kl = k.lower()
            if any(x in kl for x in ("price", "sell", "cost", "amount", "rub")):
                print(f"  card_data['{k}'] = {str(card_data[k])[:300]}")
                report[f"card_{k}"] = str(card_data[k])[:500]
    else:
        print("  card.json: None")
        report["card_selling_keys"] = "None"

    # 2. Price endpoints
    print("\n--- 2. Price endpoints ---")
    endpoints = [
        ("v1",        f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("v2",        f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("v2_nospp",  f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm={nm_id}"),
        ("v4",        f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("v5",        f"https://card.wb.ru/cards/v5/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("batch_v2",  f"https://card.wb.ru/cards/v2/batch?appType=1&curr=rub&dest=-1257786&nm={nm_id}"),
        ("web_v1",    f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"),
        ("web_v2",    f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={nm_id}"),
    ]
    price_results = {}
    for label, url in endpoints:
        try:
            resp = wb._http_get(url, headers=JSON_HEADERS, timeout=12, fast=True)
            if resp is None:
                print(f"  {label}: None")
                price_results[label] = "None"
            else:
                body_preview = resp.text[:200] if hasattr(resp, "text") else ""
                print(f"  {label}: HTTP {resp.status_code}, {len(resp.content)}B")
                entry = {"http": resp.status_code, "bytes": len(resp.content)}
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        entry["keys"] = list(data.keys())[:10]
                        prods = data.get("products") or (data.get("data", {}).get("items", []) if isinstance(data.get("data"), dict) else [])
                        if prods:
                            p = prods[0]
                            entry["product_keys"] = list(p.keys())[:15]
                            sizes = p.get("sizes", [])
                            if sizes:
                                entry["size0"] = json.dumps(sizes[0], ensure_ascii=False)[:300]
                                print(f"    sizes[0]: {json.dumps(sizes[0], ensure_ascii=False)[:200]}")
                        else:
                            entry["note"] = "no products"
                    except Exception as e2:
                        entry["parse_err"] = str(e2)
                        entry["body200"] = body_preview
                else:
                    entry["body200"] = body_preview
                price_results[label] = entry
        except Exception as e:
            print(f"  {label}: EXC {e}")
            price_results[label] = f"EXC {e}"
        time.sleep(1.5)
    report["price_endpoints"] = price_results

    # 3. HTML price patterns
    print("\n--- 3. HTML price patterns ---")
    html_url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
    html_resp = wb._http_get(html_url, headers=HTML_HEADERS, timeout=20)
    html_patterns = {}
    if html_resp and html_resp.status_code == 200:
        html = html_resp.text
        print(f"  HTML: {len(html)}B")
        patterns = [
            ("og_price",      r'og:price:amount[^>]*content="([\d.]+)"'),
            ("data_price",    r'data-price="(\d+)"'),
            ("price_block",   r'class="[^"]*price[^"]*"[^>]*>\s*([\d\s\u00a0]{2,20})\s*<'),
            ("rub_text",      r'>\s*([\d\s\u00a0]{3,15})\s*(?:\u20bd|руб)\s*<'),
            ("jsonld_price",  r'"price"\s*:\s*"?([\d.]+)"?'),
            ("jsonld_offer",  r'"offers"\s*:\s*\{[^}]{0,300}'),
            ("state_json",    r'window\.__STATE__\s*=\s*(\{.{0,500})'),
        ]
        for name, pat in patterns:
            matches = re.findall(pat, html, re.I)
            html_patterns[name] = matches[:5] if matches else []
            print(f"  {name}: {matches[:3] if matches else '(none)'}")
        # Check for embedded JSON state
        m = re.search(r'window\.__STATE__\s*=\s*(\{)', html)
        if m:
            print(f"  __STATE__ found at pos {m.start()}")
            html_patterns["state_found"] = True
    else:
        code = html_resp.status_code if html_resp else "None"
        print(f"  HTML: HTTP {code}")
        html_patterns["http"] = code
    report["html_price"] = html_patterns

# ============================================================
# FARPOST
# ============================================================
print("\n" + "=" * 60)
print("FARPOST FETCH DIAGNOSTIC")
print("=" * 60)

fp = None
try:
    from r52.collectors.farpost import FarpostCollector
    fp = FarpostCollector(data_dir=None, use_proxies=False)
except Exception as e:
    print(f"FP import/init error: {e}")
    report["fp_error"] = str(e)

if fp:
    bid = "130693618"
    burl = f"https://www.farpost.ru/bulletin/{bid}"
    fp_results = {}

    # 4. Warmup
    print("\n--- 4. Warmup ---")
    try:
        wresp = fp.session.get("https://www.farpost.ru/", timeout=(10, 30))
        print(f"  HTTP {wresp.status_code}, {len(wresp.content)}B")
        fp_results["warmup"] = {"http": wresp.status_code, "bytes": len(wresp.content)}
        cookies = dict(fp.session.cookies)
        print(f"  Cookies: {list(cookies.keys())}")
        fp_results["cookies"] = list(cookies.keys())
    except Exception as e:
        print(f"  Error: {e}")
        fp_results["warmup"] = f"EXC {e}"
    time.sleep(2)

    # 5. base.fetch (with throttle)
    print("\n--- 5. base.fetch ---")
    try:
        r5 = fp.fetch(burl)
        if r5:
            print(f"  HTTP {r5.status_code}, {len(r5.content)}B, final={r5.url}")
            fp_results["base_fetch"] = {"http": r5.status_code, "bytes": len(r5.content), "url": r5.url}
            print(f"  body[:300]: {r5.text[:300]}")
            fp_results["body300"] = r5.text[:300]
        else:
            print("  None")
            fp_results["base_fetch"] = "None"
    except Exception as e:
        print(f"  EXC: {e}")
        fp_results["base_fetch"] = f"EXC {e}"
    time.sleep(3)

    # 6. Direct session.get (no throttle)
    print("\n--- 6. Direct session.get ---")
    try:
        r6 = fp.session.get(burl, timeout=(10, 30), allow_redirects=True)
        print(f"  HTTP {r6.status_code}, {len(r6.content)}B, final={r6.url}")
        fp_results["direct"] = {"http": r6.status_code, "bytes": len(r6.content), "url": r6.url}
        print(f"  body[:300]: {r6.text[:300]}")
        fp_results["direct_body300"] = r6.text[:300]
    except Exception as e:
        print(f"  EXC: {e}")
        fp_results["direct"] = f"EXC {e}"
    time.sleep(3)

    # 7. curl_cffi
    print("\n--- 7. curl_cffi ---")
    try:
        from curl_cffi import requests as cffi_req
        r7 = cffi_req.get(burl, impersonate="chrome", timeout=20, allow_redirects=True)
        print(f"  HTTP {r7.status_code}, {len(r7.content)}B, final={r7.url}")
        fp_results["cffi"] = {"http": r7.status_code, "bytes": len(r7.content), "url": r7.url}
        print(f"  body[:300]: {r7.text[:300]}")
        fp_results["cffi_body300"] = r7.text[:300]
    except Exception as e:
        print(f"  EXC: {e}")
        fp_results["cffi"] = f"EXC {e}"

    report["farpost"] = fp_results

# Save
out = Path(__file__).parent / "diag_v106_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nReport: {out}")
print("DONE")
