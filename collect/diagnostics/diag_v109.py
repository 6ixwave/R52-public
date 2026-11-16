"""Diagnostic v109: curl_cffi for Ozon, real IDs for Farpost/Avito, WB price deep-dive."""
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v109")

report = {}

# ============================================================
# 1. WB — deep price extraction from card.json
# ============================================================
print("=" * 60)
print("1. WB PRICE (card.json deep)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    s = cffi_req.Session(impersonate="chrome124")
    nm_id = 199298871
    vol = nm_id // 100000
    part = nm_id // 1000
    host = "basket-13.wbbasket.ru"
    card_url = f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    print(f"  URL: {card_url}")
    resp = s.get(card_url, timeout=15)
    print(f"  HTTP {resp.status_code}, {len(resp.content)}B")
    wb_entry = {"http": resp.status_code, "bytes": len(resp.content)}
    if resp.status_code == 200:
        data = resp.json()
        wb_entry["all_keys"] = list(data.keys())
        print(f"  keys: {list(data.keys())}")
        # selling
        selling = data.get("selling", {})
        wb_entry["selling"] = selling
        print(f"  selling: {json.dumps(selling, ensure_ascii=False)[:500]}")
        # options — ALL
        opts = data.get("options", [])
        wb_entry["options_count"] = len(opts)
        for o in opts[:30]:
            if isinstance(o, dict):
                name = o.get("name", "")
                val = o.get("value", "")
                if any(x in name.lower() for x in ("цена", "price", "стоимост", "sale", "скидк")):
                    wb_entry.setdefault("price_opts", []).append({"name": name, "value": str(val)[:100]})
        # brand, name
        wb_entry["brand"] = data.get("brand", "")
        wb_entry["name"] = data.get("name", "")
        # Check for nested price structures
        for k in ("price", "prices", "salePrice", "listPrice", "priceU", "priceSale"):
            if k in data:
                wb_entry[f"direct_{k}"] = data[k]
        # Check in options for numeric values > 100
        for o in opts:
            if isinstance(o, dict):
                v = o.get("value", "")
                try:
                    num = float(str(v).replace(",", ".").replace(" ", ""))
                    if num > 100:
                        wb_entry.setdefault("numeric_opts", []).append({"name": o.get("name",""), "val": num})
                except (ValueError, TypeError):
                    pass
        report["wb"] = wb_entry
        print(f"  brand: {wb_entry.get('brand')}")
        print(f"  name: {wb_entry.get('name','')[:60]}")
        print(f"  selling: {json.dumps(selling, ensure_ascii=False)[:200]}")
        print(f"  price_opts: {wb_entry.get('price_opts', [])}")
        print(f"  numeric_opts: {wb_entry.get('numeric_opts', [])[:5]}")
except Exception as e:
    import traceback; traceback.print_exc()
    report["wb_error"] = str(e)

# ============================================================
# 2. OZON — curl_cffi chrome124
# ============================================================
print("\n" + "=" * 60)
print("2. OZON (curl_cffi chrome124)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    os_s = cffi_req.Session(impersonate="chrome124")
    # Real product IDs from search: 5699176004 (e-bike), plus common ones
    oz_tests = [
        "5699176004",   # Электровелосипед 2026
        "2198727557",   # iPhone 15
        "1162442743",   # Samsung
    ]
    for oid in oz_tests:
        url = f"https://www.ozon.ru/product/{oid}/"
        print(f"\n  [{oid}]")
        try:
            r = os_s.get(url, timeout=(10, 30), headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            })
            print(f"    HTTP {r.status_code}, {len(r.content)}B, final={r.url[:80]}")
            entry = {"http": r.status_code, "bytes": len(r.content), "final": r.url}
            if r.status_code == 200 and len(r.text) > 1000:
                html = r.text
                # JSON-LD
                ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
                entry["jsonld_count"] = len(ld_blocks)
                for lb in ld_blocks[:2]:
                    try:
                        ld = json.loads(lb)
                        if isinstance(ld, dict):
                            entry["jsonld_type"] = ld.get("@type", "")
                            entry["jsonld_name"] = ld.get("name", "")[:80]
                            entry["jsonld_price"] = str(ld.get("offers", {}).get("price", ""))[:50] if isinstance(ld.get("offers"), dict) else str(ld.get("offers",""))[:100]
                    except:
                        pass
                # og: tags
                og_title = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
                og_price = re.search(r'<meta[^>]*property="og:price:amount"[^>]*content="([^"]*)"', html)
                entry["og_title"] = og_title.group(1)[:80] if og_title else None
                entry["og_price"] = og_price.group(1) if og_price else None
                # data-widget
                dw = re.search(r'data-widget="[^"]*product[^"]*"', html)
                entry["has_data_widget"] = bool(dw)
                # window.__STATE__
                state_m = re.search(r'window\.__STATE__\s*=\s*(\{.*?\});?\s*</script>', html, re.DOTALL)
                entry["has_state"] = bool(state_m)
                # price patterns
                price_pats = re.findall(r'(\d[\d\s]{2,12})\s*(?:₽|руб)', html[:50000])
                entry["price_patterns"] = price_pats[:5]
                print(f"    og_title: {entry.get('og_title')}")
                print(f"    og_price: {entry.get('og_price')}")
                print(f"    jsonld: {entry.get('jsonld_type')} / {entry.get('jsonld_name','')[:50]}")
                print(f"    price_pats: {entry.get('price_patterns')}")
            elif r.status_code == 403:
                entry["note"] = "blocked"
                # Check if it's a challenge page
                entry["body300"] = r.text[:300]
                print(f"    BLOCKED 403")
            report[f"ozon_{oid}"] = entry
        except Exception as e:
            print(f"    EXC: {e}")
            report[f"ozon_{oid}"] = {"exc": str(e)}
        time.sleep(3)
except Exception as e:
    import traceback; traceback.print_exc()
    report["ozon_error"] = str(e)

# ============================================================
# 3. FARPOST — correct listing + real bulletin
# ============================================================
print("\n" + "=" * 60)
print("3. FARPOST (find live bulletins)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    fp_s = cffi_req.Session(impersonate="chrome124")
    # Try multiple listing URLs
    fp_urls = [
        "https://www.farpost.ru/",
        "https://www.farpost.ru/vladivostok/",
        "https://www.farpost.ru/list/",
        "https://www.farpost.ru/list/1/",
        "https://www.farpost.ru/search?query=%D0%B1%D0%B5%D0%BD%D0%B7",
    ]
    live_ids = []
    for fu in fp_urls:
        print(f"\n  Try: {fu}")
        try:
            r = fp_s.get(fu, timeout=(10, 25), headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
            })
            print(f"    HTTP {r.status_code}, {len(r.content)}B")
            if r.status_code == 200:
                ids = re.findall(r'/bulletin/(\d{6,})', r.text)
                new_ids = [i for i in ids if i not in live_ids]
                live_ids.extend(new_ids)
                print(f"    IDs found: {len(ids)} total, {len(new_ids)} new")
                if len(live_ids) >= 3:
                    break
        except Exception as e:
            print(f"    EXC: {e}")
        time.sleep(1)

    report["fp_listing"] = {"urls_tried": len(fp_urls), "live_ids": live_ids[:10]}
    print(f"\n  Total live IDs: {len(live_ids)}")

    # Test first 2 live IDs
    for bid in live_ids[:2]:
        burl = f"https://www.farpost.ru/bulletin/{bid}"
        print(f"\n  Bulletin {bid}: {burl}")
        try:
            r2 = fp_s.get(burl, timeout=(10, 25), headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Referer": "https://www.farpost.ru/",
            })
            print(f"    HTTP {r2.status_code}, {len(r2.content)}B")
            entry = {"http": r2.status_code, "bytes": len(r2.content)}
            if r2.status_code == 200 and len(r2.text) > 1000:
                html = r2.text
                # JSON-LD
                ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
                entry["jsonld_count"] = len(ld_blocks)
                for lb in ld_blocks[:3]:
                    try:
                        ld = json.loads(lb)
                        if isinstance(ld, dict) and ld.get("@type") in ("Product", "Offer", "IndividualProduct"):
                            entry["jsonld_type"] = ld.get("@type")
                            entry["jsonld_name"] = ld.get("name", "")[:80]
                            offers = ld.get("offers", {})
                            if isinstance(offers, dict):
                                entry["jsonld_price"] = offers.get("price", offers.get("lowPrice", ""))
                                entry["jsonld_currency"] = offers.get("priceCurrency", "")
                            elif isinstance(offers, list) and offers:
                                entry["jsonld_price"] = offers[0].get("price", "")
                    except:
                        pass
                # data-bulletin-price
                bp = re.search(r'data-bulletin-price="([^"]*)"', html)
                entry["data_price"] = bp.group(1) if bp else None
                # og tags
                og_t = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
                entry["og_title"] = og_t.group(1)[:80] if og_t else None
                # price in HTML
                price_html = re.findall(r'(\d[\d\s]{2,10})\s*(?:₽|руб)', html[:50000])
                entry["price_html"] = price_html[:5]
                # baza.lightbox
                lb_init = re.search(r'baza\.lightbox\.init\((\[.*?\])\)', html, re.DOTALL)
                entry["has_lightbox"] = bool(lb_init)
                print(f"    jsonld: {entry.get('jsonld_type')} / {entry.get('jsonld_name','')[:50]}")
                print(f"    data_price: {entry.get('data_price')}")
                print(f"    og_title: {entry.get('og_title')}")
                print(f"    price_html: {entry.get('price_html')}")
            report[f"fp_{bid}"] = entry
        except Exception as e:
            print(f"    EXC: {e}")
            report[f"fp_{bid}"] = {"exc": str(e)}
        time.sleep(2)
except Exception as e:
    import traceback; traceback.print_exc()
    report["farpost_error"] = str(e)

# ============================================================
# 4. AVITO — curl_cffi + real search
# ============================================================
print("\n" + "=" * 60)
print("4. AVITO (curl_cffi + real IDs)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    av_s = cffi_req.Session(impersonate="chrome124")
    # Search for real ads
    search_url = "https://www.avito.ru/all?q=%D0%B1%D0%B5%D0%BD%D0%B7&sort=price_desc"
    print(f"  Search: {search_url}")
    r = av_s.get(search_url, timeout=(10, 30), headers={
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9",
    })
    print(f"  HTTP {r.status_code}, {len(r.content)}B")
    av_ids = []
    if r.status_code == 200:
        av_ids = re.findall(r'/ad/(\d{8,})', r.text)
        av_ids = list(dict.fromkeys(av_ids))[:5]
        print(f"  Real IDs: {av_ids}")
    report["av_search"] = {"http": r.status_code, "ids": av_ids}

    # Test first 2
    for aid in av_ids[:2]:
        aurl = f"https://www.avito.ru/ad/{aid}"
        print(f"\n  Ad {aid}: {aurl}")
        try:
            r2 = av_s.get(aurl, timeout=(10, 30), headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Referer": "https://www.avito.ru/all",
            })
            print(f"    HTTP {r2.status_code}, {len(r2.content)}B")
            entry = {"http": r2.status_code, "bytes": len(r2.content)}
            if r2.status_code == 200 and len(r2.text) > 1000:
                html = r2.text
                # JSON-LD
                ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
                entry["jsonld_count"] = len(ld_blocks)
                for lb in ld_blocks[:3]:
                    try:
                        ld = json.loads(lb)
                        if isinstance(ld, dict):
                            t = ld.get("@type", "")
                            if t in ("Product", "Offer", "IndividualProduct", "CreativeWork"):
                                entry["jsonld_type"] = t
                                entry["jsonld_name"] = ld.get("name", "")[:80]
                                offers = ld.get("offers", {})
                                if isinstance(offers, dict):
                                    entry["jsonld_price"] = offers.get("price", "")
                                    entry["jsonld_currency"] = offers.get("priceCurrency", "")
                                elif isinstance(offers, list) and offers:
                                    entry["jsonld_price"] = offers[0].get("price", "")
                    except:
                        pass
                # __PRELOADED_STATE__
                ps = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>', html, re.DOTALL)
                entry["has_preloaded"] = bool(ps)
                # og
                og_t = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
                entry["og_title"] = og_t.group(1)[:80] if og_t else None
                # price
                price_html = re.findall(r'(\d[\d\s]{2,10})\s*(?:₽|руб)', html[:50000])
                entry["price_html"] = price_html[:5]
                # captcha check
                entry["captcha"] = bool(re.search(r'(captcha|altcha|challenge|verify)', html[:5000], re.I))
                print(f"    jsonld: {entry.get('jsonld_type')} / {entry.get('jsonld_name','')[:50]}")
                print(f"    og_title: {entry.get('og_title')}")
                print(f"    price_html: {entry.get('price_html')}")
                print(f"    captcha: {entry.get('captcha')}")
            elif r2.status_code in (403, 429):
                entry["note"] = "blocked"
                entry["captcha"] = bool(re.search(r'(captcha|altcha|challenge)', r2.text[:5000], re.I))
            report[f"av_{aid}"] = entry
        except Exception as e:
            print(f"    EXC: {e}")
            report[f"av_{aid}"] = {"exc": str(e)}
        time.sleep(3)
except Exception as e:
    import traceback; traceback.print_exc()
    report["avito_error"] = str(e)

# Save
out = Path(__file__).parent / "diag_v109_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nReport: {out}")
print("DONE")
