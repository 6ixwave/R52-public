"""Diagnostic v107: WB price (headers), Farpost (multiple IDs), Ozon, Avito."""
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v107")

report = {}

# ============================================================
# 1. WB PRICE — different headers
# ============================================================
print("=" * 60)
print("1. WB PRICE (varied headers)")
print("=" * 60)

nm_id = 199298871
vol = nm_id // 100000
part = nm_id // 1000
card_path = f"/vol{vol}/part{part}/{nm_id}/info/ru/card.json"

try:
    from r52.collectors.wb import WBCollector
    wb = WBCollector(data_dir=None, use_proxies=False)
except Exception as e:
    print(f"WB init error: {e}")
    wb = None

if wb:
    # Find basket host first
    basket_host = wb._find_basket_host(nm_id, vol, card_path)
    print(f"  basket host: {basket_host}")

    price_tests = []

    # A. card.wb.ru with Referer
    headers_a = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
        "Origin": "https://www.wildberries.ru",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    for label, url in [
        ("card_v2_ref", f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
        ("card_v1_ref", f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"),
    ]:
        try:
            resp = wb.session.get(url, headers=headers_a, timeout=12)
            entry = {"http": resp.status_code, "bytes": len(resp.content)}
            if resp.status_code == 200:
                data = resp.json()
                prods = data.get("products", [])
                if prods:
                    p = prods[0]
                    entry["sizes"] = json.dumps(p.get("sizes", [])[:1], ensure_ascii=False)[:400]
                    entry["price_keys"] = list(p.get("price", {}).keys()) if p.get("price") else []
            print(f"  {label}: {resp.status_code} {len(resp.content)}B")
            price_tests.append({"label": label, **entry})
        except Exception as e:
            print(f"  {label}: EXC {e}")
            price_tests.append({"label": label, "exc": str(e)})
        time.sleep(1)

    # B. web_api with full browser headers
    headers_b = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
        "Origin": "https://www.wildberries.ru",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }
    for label, url in [
        ("web_v1_full", f"https://www.wildberries.ru/web/api/catalog/cards/v1/detail?nm={nm_id}"),
        ("web_v2_full", f"https://www.wildberries.ru/web/api/catalog/cards/v2/detail?nm={nm_id}"),
    ]:
        try:
            resp = wb.session.get(url, headers=headers_b, timeout=12)
            entry = {"http": resp.status_code, "bytes": len(resp.content)}
            if resp.status_code == 200:
                data = resp.json()
                entry["keys"] = list(data.keys())[:10]
                entry["preview"] = json.dumps(data, ensure_ascii=False)[:400]
            print(f"  {label}: {resp.status_code} {len(resp.content)}B")
            price_tests.append({"label": label, **entry})
        except Exception as e:
            print(f"  {label}: EXC {e}")
            price_tests.append({"label": label, "exc": str(e)})
        time.sleep(1)

    # C. HTML with full headers (check 498)
    headers_c = dict(wb.session.headers)
    headers_c["Referer"] = "https://www.wildberries.ru/"
    try:
        resp = wb.session.get(f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                              headers=headers_c, timeout=20)
        print(f"  HTML: {resp.status_code} {len(resp.content)}B")
        html_entry = {"http": resp.status_code, "bytes": len(resp.content)}
        if resp.status_code == 200:
            html = resp.text
            # Extract price patterns
            pats = {
                "og_price": re.findall(r'og:price:amount[^>]*content="([\d.]+)"', html, re.I),
                "data_price": re.findall(r'data-price="(\d+)"', html, re.I),
                "rub_text": re.findall(r'>\s*([\d\s\u00a0]{3,15})\s*(?:\u20bd|руб)\s*<', html),
                "jsonld_price": re.findall(r'"price"\s*:\s*"?([\d.]+)"?', html),
            }
            html_entry["patterns"] = {k: v[:3] for k, v in pats.items()}
            # Check __STATE__ or similar
            for sm in re.finditer(r'window\.__\w+__\s*=', html):
                html_entry["state_vars"] = html[sm.start():sm.start()+80]
                break
        print(f"  HTML patterns: {json.dumps(html_entry.get('patterns', {}), ensure_ascii=False)[:300]}")
        price_tests.append({"label": "html", **html_entry})
    except Exception as e:
        print(f"  HTML: EXC {e}")
        price_tests.append({"label": "html", "exc": str(e)})

    # D. basket CDN: maybe there's a price in the card.json media or elsewhere
    # Also try: https://static-basket-{n}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json
    # And the "nm" endpoint
    try:
        resp = wb.session.get(
            f"https://basket-{16:02d}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/info/ru/card.json",
            headers={"Accept": "application/json"}, timeout=12)
        print(f"  basket_direct: {resp.status_code} {len(resp.content)}B")
    except Exception as e:
        print(f"  basket_direct: EXC {e}")

    report["wb_price"] = price_tests

# ============================================================
# 2. FARPOST — multiple bulletin IDs
# ============================================================
print("\n" + "=" * 60)
print("2. FARPOST (multiple IDs)")
print("=" * 60)

try:
    from r52.collectors.farpost import FarpostCollector
    fp = FarpostCollector(data_dir=None, use_proxies=False)
    # Warmup
    fp._warmup()
except Exception as e:
    print(f"FP init error: {e}")
    fp = None

if fp:
    # Test IDs: original + a few recent ones
    test_ids = ["130693618", "139122082", "140000001", "138000000"]
    fp_results = {}
    for bid in test_ids:
        burl = f"https://www.farpost.ru/bulletin/{bid}"
        try:
            resp = fp.session.get(burl, timeout=(10, 20), allow_redirects=True)
            entry = {
                "http": resp.status_code,
                "bytes": len(resp.content),
                "final_url": resp.url,
            }
            if resp.status_code == 200 and len(resp.text) > 1000:
                # Check for product content
                has_price = bool(re.search(r'data-bulletin-price', resp.text))
                has_jsonld = bool(re.search(r'application/ld\+json', resp.text))
                has_gallery = bool(re.search(r'data-image-info', resp.text))
                entry["has_price"] = has_price
                entry["has_jsonld"] = has_jsonld
                entry["has_gallery"] = has_gallery
                entry["title_match"] = re.search(r'<h1[^>]*>.*?<span[^>]*>(.*?)</span>', resp.text, re.DOTALL)
                if entry["title_match"]:
                    entry["title"] = re.sub(r'<[^>]+>', '', entry["title_match"].group(1)).strip()[:80]
            elif resp.status_code == 404:
                entry["note"] = "404 - bulletin expired/removed"
            print(f"  {bid}: HTTP {resp.status_code}, {len(resp.content)}B, "
                  f"price={entry.get('has_price')}, jsonld={entry.get('has_jsonld')}")
            fp_results[bid] = entry
        except Exception as e:
            print(f"  {bid}: EXC {e}")
            fp_results[bid] = {"exc": str(e)}
        time.sleep(2)
    report["farpost"] = fp_results

# ============================================================
# 3. OZON — E2E test
# ============================================================
print("\n" + "=" * 60)
print("3. OZON E2E")
print("=" * 60)

try:
    from r52.collectors.ozon import OzonCollector
    oz = OzonCollector(data_dir=None, use_proxies=False)
    # Test product ID (common one)
    oz_id = "2198727557"
    oz_url = oz.build_url(oz_id)
    print(f"  URL: {oz_url}")
    resp = oz.fetch(oz_url)
    if resp:
        print(f"  HTTP {resp.status_code}, {len(resp.content)}B")
        obs = oz.parse_page(resp, oz_id, oz_url, "")
        if obs:
            fields = obs.get("fields", {})
            print(f"  fields: {list(fields.keys())}")
            for k in ("title", "price_candidates", "media", "availability"):
                if k in fields:
                    print(f"    {k}: {str(fields[k])[:150]}")
            print(f"  price_candidates: {json.dumps(obs.get('price_candidates', []), ensure_ascii=False)[:300]}")
            media = obs.get("media", {}).get("items", [])
            print(f"  media: {len(media)} items")
            report["ozon"] = {
                "id": oz_id, "http": resp.status_code,
                "fields": list(fields.keys()),
                "prices": obs.get("price_candidates", [])[:3],
                "media_count": len(media),
                "availability": obs.get("availability"),
            }
        else:
            print("  parse_page: None")
            report["ozon"] = {"id": oz_id, "http": resp.status_code, "parse": "None"}
    else:
        print("  fetch: None")
        report["ozon"] = {"id": oz_id, "fetch": "None"}
except Exception as e:
    import traceback
    print(f"  OZON error: {e}")
    traceback.print_exc()
    report["ozon"] = {"error": str(e)}

# ============================================================
# 4. AVITO — E2E test
# ============================================================
print("\n" + "=" * 60)
print("4. AVITO E2E")
print("=" * 60)

try:
    from r52.collectors.avito import AvitoCollector
    av = AvitoCollector(data_dir=None, use_proxies=False)
    # Avito ad ID (need a real one; try a common format)
    av_id = "1050000000"
    av_url = av.build_url(av_id)
    print(f"  URL: {av_url}")
    resp = av.fetch(av_url)
    if resp:
        print(f"  HTTP {resp.status_code}, {len(resp.content)}B")
        # Check captcha
        html = resp.text
        is_captcha = av._is_captcha_page(html)
        print(f"  captcha: {is_captcha}")
        if not is_captcha:
            obs = av.parse_page(resp, av_id, av_url, "")
            if obs:
                fields = obs.get("fields", {})
                print(f"  fields: {list(fields.keys())}")
                for k in ("title", "description", "category"):
                    if k in fields:
                        print(f"    {k}: {str(fields[k].get('value', ''))[:120]}")
                print(f"  prices: {json.dumps(obs.get('price_candidates', []), ensure_ascii=False)[:200]}")
                media = obs.get("media", {}).get("items", [])
                print(f"  media: {len(media)} items")
                report["avito"] = {
                    "id": av_id, "http": resp.status_code,
                    "fields": list(fields.keys()),
                    "prices": obs.get("price_candidates", [])[:3],
                    "media_count": len(media),
                    "availability": obs.get("availability"),
                }
            else:
                print("  parse_page: None")
                report["avito"] = {"id": av_id, "http": resp.status_code, "parse": "None"}
        else:
            report["avito"] = {"id": av_id, "http": resp.status_code, "captcha": True}
    else:
        print("  fetch: None")
        report["avito"] = {"id": av_id, "fetch": "None"}
except Exception as e:
    import traceback
    print(f"  AVITO error: {e}")
    traceback.print_exc()
    report["avito"] = {"error": str(e)}

# Save
out = Path(__file__).parent / "diag_v107_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nReport saved: {out}")
print("DONE")
