"""Diagnostic v108: Fast E2E with real IDs, bypass throttle, direct session."""
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v108")

report = {}

# ============================================================
# 1. OZON — direct session, real product
# ============================================================
print("=" * 60)
print("1. OZON")
print("=" * 60)

try:
    from r52.collectors.ozon import OzonCollector
    oz = OzonCollector(data_dir=None, use_proxies=False)

    # Real product: iPhone 15 (common on Ozon)
    oz_tests = [
        ("2198727557", "iPhone 15"),
        ("1162442743", "Samsung Galaxy"),
        ("2652786521", "Xiaomi"),
    ]
    for oid, hint in oz_tests:
        url = f"https://www.ozon.ru/product/{oid}/"
        print(f"\n  [{oid}] {hint}")
        try:
            resp = oz.session.get(url, timeout=(10, 25), allow_redirects=True)
            print(f"    HTTP {resp.status_code}, {len(resp.content)}B, final={resp.url[:80]}")
            entry = {"http": resp.status_code, "bytes": len(resp.content), "final": resp.url}
            if resp.status_code == 200 and len(resp.text) > 500:
                obs = oz.parse_page(resp, oid, url, hint)
                if obs:
                    fields = obs.get("fields", {})
                    entry["fields"] = list(fields.keys())
                    entry["title"] = fields.get("title", {}).get("value", "")[:80]
                    entry["prices"] = obs.get("price_candidates", [])[:3]
                    entry["media_count"] = len(obs.get("media", {}).get("items", []))
                    entry["availability"] = obs.get("availability", "UNKNOWN")
                    print(f"    fields: {list(fields.keys())}")
                    print(f"    title: {entry['title']}")
                    print(f"    prices: {json.dumps(entry['prices'], ensure_ascii=False)[:200]}")
                    print(f"    media: {entry['media_count']}")
                else:
                    entry["parse"] = "None"
                    print(f"    parse: None")
            elif resp.status_code == 403:
                entry["note"] = "blocked"
                print(f"    BLOCKED (403)")
            else:
                entry["body200"] = resp.text[:200]
            report[f"ozon_{oid}"] = entry
        except Exception as e:
            print(f"    EXC: {e}")
            report[f"ozon_{oid}"] = {"exc": str(e)}
        time.sleep(2)
except Exception as e:
    import traceback; traceback.print_exc()
    report["ozon_error"] = str(e)

# ============================================================
# 2. AVITO — direct session, real ad
# ============================================================
print("\n" + "=" * 60)
print("2. AVITO")
print("=" * 60)

try:
    from r52.collectors.avito import AvitoCollector
    av = AvitoCollector(data_dir=None, use_proxies=False)

    av_tests = [
        ("1050000000", "test1"),
        ("1123456789", "test2"),
        ("1000000001", "test3"),
    ]
    for aid, hint in av_tests:
        url = f"https://www.avito.ru/ad/{aid}"
        print(f"\n  [{aid}] {hint}")
        try:
            resp = av.session.get(url, timeout=(10, 25), allow_redirects=True)
            print(f"    HTTP {resp.status_code}, {len(resp.content)}B, final={resp.url[:80]}")
            entry = {"http": resp.status_code, "bytes": len(resp.content), "final": resp.url}
            if resp.status_code == 200 and len(resp.text) > 500:
                html = resp.text
                is_captcha = av._is_captcha_page(html)
                entry["captcha"] = is_captcha
                if is_captcha:
                    print(f"    CAPTCHA!")
                else:
                    obs = av.parse_page(resp, aid, url, hint)
                    if obs:
                        fields = obs.get("fields", {})
                        entry["fields"] = list(fields.keys())
                        entry["title"] = fields.get("title", {}).get("value", "")[:80]
                        entry["prices"] = obs.get("price_candidates", [])[:3]
                        entry["media_count"] = len(obs.get("media", {}).get("items", []))
                        print(f"    fields: {list(fields.keys())}")
                        print(f"    title: {entry['title']}")
                    else:
                        entry["parse"] = "None"
            elif resp.status_code in (403, 429):
                entry["note"] = "blocked/captcha"
            else:
                entry["body200"] = resp.text[:200]
            report[f"avito_{aid}"] = entry
        except Exception as e:
            print(f"    EXC: {e}")
            report[f"avito_{aid}"] = {"exc": str(e)}
        time.sleep(2)
except Exception as e:
    import traceback; traceback.print_exc()
    report["avito_error"] = str(e)

# ============================================================
# 3. FARPOST — find live bulletin via search page
# ============================================================
print("\n" + "=" * 60)
print("3. FARPOST (search for live bulletin)")
print("=" * 60)

try:
    from r52.collectors.farpost import FarpostCollector
    fp = FarpostCollector(data_dir=None, use_proxies=False)

    # First: hit search/listing page to find a live bulletin
    print("  Step 1: search page")
    search_url = "https://www.farpost.ru/list/1"  # main listing
    resp = fp.session.get(search_url, timeout=(10, 25))
    print(f"    HTTP {resp.status_code}, {len(resp.content)}B")
    live_ids = []
    if resp.status_code == 200:
        # Extract bulletin IDs from links
        ids = re.findall(r'/bulletin/(\d{6,})', resp.text)
        live_ids = list(dict.fromkeys(ids))[:5]  # unique, first 5
        print(f"    Found {len(live_ids)} live IDs: {live_ids}")
    report["fp_search"] = {"http": resp.status_code, "ids": live_ids}
    time.sleep(2)

    # Test first live ID
    if live_ids:
        bid = live_ids[0]
        burl = f"https://www.farpost.ru/bulletin/{bid}"
        print(f"\n  Step 2: bulletin {bid}")
        resp2 = fp.session.get(burl, timeout=(10, 25), allow_redirects=True)
        print(f"    HTTP {resp2.status_code}, {len(resp2.content)}B")
        entry = {"http": resp2.status_code, "bytes": len(resp2.content)}
        if resp2.status_code == 200 and len(resp2.text) > 1000:
            obs = fp.parse_page(resp2, bid, burl, "")
            if obs:
                fields = obs.get("fields", {})
                entry["fields"] = list(fields.keys())
                entry["title"] = fields.get("title", {}).get("value", "")[:80]
                entry["prices"] = obs.get("price_candidates", [])[:3]
                entry["media_count"] = len(obs.get("media", {}).get("items", []))
                entry["availability"] = obs.get("availability", "UNKNOWN")
                print(f"    fields: {list(fields.keys())}")
                print(f"    title: {entry['title']}")
                print(f"    prices: {json.dumps(entry['prices'], ensure_ascii=False)[:200]}")
                print(f"    media: {entry['media_count']}")
            else:
                entry["parse"] = "None"
                # Debug: check what's in HTML
                html = resp2.text
                entry["has_price_attr"] = bool(re.search(r'data-bulletin-price', html))
                entry["has_jsonld"] = bool(re.search(r'application/ld\+json', html))
                entry["has_gallery"] = bool(re.search(r'data-image-info', html))
                entry["body500"] = html[:500]
                print(f"    parse: None, price_attr={entry['has_price_attr']}, jsonld={entry['has_jsonld']}")
        report["fp_bulletin"] = entry
except Exception as e:
    import traceback; traceback.print_exc()
    report["farpost_error"] = str(e)

# ============================================================
# 4. WB — correct basket host + price from card.json
# ============================================================
print("\n" + "=" * 60)
print("4. WB (basket-13 + price check)")
print("=" * 60)

try:
    from r52.collectors.wb import WBCollector
    wb = WBCollector(data_dir=None, use_proxies=False)
    nm_id = 199298871
    vol = nm_id // 100000
    part = nm_id // 1000

    # Correct host from v106: basket-13
    host = "basket-13.wbbasket.ru"
    card_url = f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    print(f"  card.json: {card_url}")
    resp = wb.session.get(card_url, timeout=12, headers={"Accept": "application/json"})
    print(f"    HTTP {resp.status_code}, {len(resp.content)}B")
    wb_entry = {"card_http": resp.status_code, "card_bytes": len(resp.content)}
    if resp.status_code == 200:
        data = resp.json()
        # Check ALL keys for price-related
        price_keys = [k for k in data.keys() if any(x in k.lower() for x in ("price", "sell", "cost", "amount", "rub", "sale"))]
        wb_entry["price_keys"] = price_keys
        wb_entry["all_keys"] = list(data.keys())
        print(f"    keys: {list(data.keys())}")
        print(f"    price-like keys: {price_keys}")
        # selling
        selling = data.get("selling", {})
        wb_entry["selling"] = selling
        print(f"    selling: {json.dumps(selling, ensure_ascii=False)[:300]}")
        # options — look for price in options
        opts = data.get("options", [])
        price_opts = [o for o in opts if isinstance(o, dict) and any(x in (o.get("name","").lower()) for x in ("цена", "price", "стоимост"))]
        wb_entry["price_options"] = price_opts[:3]
    report["wb"] = wb_entry
except Exception as e:
    import traceback; traceback.print_exc()
    report["wb_error"] = str(e)

# Save
out = Path(__file__).parent / "diag_v108_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nReport: {out}")
print("DONE")
