"""Diagnostic v111: Farpost SEO URLs, Ozon cookie-first, Avito category, WB price API."""
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v111")

report = {}

# ============================================================
# 1. FARPOST — full SEO URLs from search + homepage structure
# ============================================================
print("=" * 60)
print("1. FARPOST (SEO URLs + homepage dump)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    fp_s = cffi_req.Session(impersonate="chrome124")
    hdrs = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    # 1a: Homepage — dump structure to find listing pattern
    r = fp_s.get("https://www.farpost.ru/", timeout=(10, 25), headers=hdrs)
    print(f"  Homepage: HTTP {r.status_code}, {len(r.content)}B")
    # Find ALL href patterns
    all_hrefs = re.findall(r'href="([^"]+)"', r.text)
    # Filter for content links (not CSS/JS/images)
    content_links = [h for h in all_hrefs if not any(x in h for x in ('.css', '.js', '.png', '.jpg', '.svg', '.ico', 'static.baza', 'resources/', '#', 'javascript:'))]
    print(f"  Total hrefs: {len(all_hrefs)}, content: {len(content_links)}")
    print(f"  Content links sample: {content_links[:15]}")
    # Look for data attributes with IDs
    data_ids = re.findall(r'data-(?:id|bulletin|item|listing)[^=]*="(\d+)"', r.text)
    print(f"  data-IDs: {data_ids[:10]}")
    # Look for JSON in scripts
    script_json = re.findall(r'<script[^>]*>(.*?)</script>', r.text, re.DOTALL)
    id_in_scripts = []
    for sj in script_json:
        found = re.findall(r'"(?:id|bulletinId|itemId)"\s*:\s*(\d{6,})', sj)
        id_in_scripts.extend(found)
    print(f"  IDs in scripts: {id_in_scripts[:10]}")
    # Dump first 2000 chars of body
    body_start = r.text.find("<body")
    report["fp_home"] = {
        "http": r.status_code, "bytes": len(r.content),
        "content_links": content_links[:20],
        "data_ids": data_ids[:10],
        "script_ids": id_in_scripts[:10],
        "body_sample": r.text[body_start:body_start+1500] if body_start > 0 else r.text[:1500],
    }
    time.sleep(2)

    # 1b: Try exact SEO URLs from web search
    seo_urls = [
        ("66422428", "https://www.farpost.ru/vladivostok/food/seafood/nerka-pbg-2025-2026-god-raznye-razmery-kamchatka-dostavka-66422428.html"),
        ("131630486", "https://www.farpost.ru/khabarovsk/service/education/finance/intensiv-izmenenija-2025-2026-131630486.html"),
        ("134164000", "https://www.farpost.ru/vladivostok/zapchasti/akkumulyator-oursun-din-180-2025-2026-134164000.html"),
        ("136391752", "https://www.farpost.ru/vladivostok/auto/security/farrop-tenet-t8-15800-136391752.html"),
    ]
    # Also try: if SEO 404, try /bulletin/ID with city prefix
    for bid, seourl in seo_urls:
        print(f"\n  [{bid}] SEO: {seourl[:70]}...")
        try:
            r2 = fp_s.get(seourl, timeout=(10, 25), headers=hdrs)
            print(f"    HTTP {r2.status_code}, {len(r2.content)}B, final={r2.url[:80]}")
            entry = {"http": r2.status_code, "bytes": len(r2.content), "final": r2.url, "url": seourl}
            if r2.status_code == 200 and len(r2.text) > 2000:
                html = r2.text
                # title
                tm = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
                entry["title"] = tm.group(1).strip()[:100] if tm else None
                # JSON-LD
                ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
                entry["jsonld_count"] = len(ld_blocks)
                for lb in ld_blocks[:5]:
                    try:
                        ld = json.loads(lb.strip())
                        if isinstance(ld, dict):
                            t = ld.get("@type", "")
                            if t in ("Product", "IndividualProduct", "Offer", "Service"):
                                entry["jsonld_type"] = t
                                entry["jsonld_name"] = ld.get("name", "")[:100]
                                offers = ld.get("offers", {})
                                if isinstance(offers, dict):
                                    entry["jsonld_price"] = offers.get("price", offers.get("lowPrice", ""))
                                    entry["jsonld_currency"] = offers.get("priceCurrency", "")
                                    entry["jsonld_avail"] = offers.get("availability", "")
                                elif isinstance(offers, list) and offers:
                                    entry["jsonld_price"] = offers[0].get("price", "")
                                # images
                                imgs = ld.get("image", [])
                                if isinstance(imgs, list):
                                    entry["jsonld_images"] = len(imgs)
                                elif isinstance(imgs, str):
                                    entry["jsonld_images"] = 1
                    except:
                        pass
                # data-bulletin-price
                bp = re.search(r'data-bulletin-price="([^"]*)"', html)
                entry["data_price"] = bp.group(1) if bp else None
                # og tags
                og_t = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
                entry["og_title"] = og_t.group(1)[:100] if og_t else None
                # visible price
                price_html = re.findall(r'(\d[\d\s]{2,10})\s*(?:₽|руб)', html[:60000])
                entry["price_html"] = price_html[:8]
                # lightbox
                lb_init = re.search(r'baza\.lightbox\.init\((\[.*?\])\)', html, re.DOTALL)
                entry["has_lightbox"] = bool(lb_init)
                # gallery
                gal = re.findall(r'data-image-info="([^"]*)"', html)
                entry["gallery_count"] = len(gal)
                # description
                desc_m = re.search(r'class="[^"]*bulletin-desc[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
                if desc_m:
                    entry["desc_sample"] = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip()[:200]

                print(f"    title: {entry.get('title')}")
                print(f"    jsonld: {entry.get('jsonld_type')} / {entry.get('jsonld_name','')[:60]}")
                print(f"    data_price: {entry.get('data_price')}")
                print(f"    price_html: {entry.get('price_html')}")
                print(f"    gallery: {entry.get('gallery_count')}")
            elif r2.status_code == 404:
                entry["note"] = "404"
                # Maybe the bulletin expired — try /bulletin/ID
                alt_url = f"https://www.farpost.ru/bulletin/{bid}"
                r3 = fp_s.get(alt_url, timeout=(10, 20), headers=hdrs)
                entry["alt_http"] = r3.status_code
                entry["alt_bytes"] = len(r3.content)
                print(f"    Alt /bulletin/: HTTP {r3.status_code}")
            report[f"fp_{bid}"] = entry
        except Exception as e:
            print(f"    EXC: {e}")
            report[f"fp_{bid}"] = {"exc": str(e)}
        time.sleep(2)

except Exception as e:
    import traceback; traceback.print_exc()
    report["farpost_error"] = str(e)

# ============================================================
# 2. OZON — cookie-first: homepage → product
# ============================================================
print("\n" + "=" * 60)
print("2. OZON (cookie-first approach)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    os_s = cffi_req.Session(impersonate="chrome124")
    hdrs_os = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    # Step 1: Hit homepage to get cookies
    print("  Step 1: homepage for cookies")
    r_home = os_s.get("https://www.ozon.ru/", timeout=(10, 25), headers=hdrs_os)
    print(f"    HTTP {r_home.status_code}, {len(r_home.content)}B")
    print(f"    Cookies: {list(os_s.cookies.keys())}")
    time.sleep(3)

    # Step 2: Try product page
    oz_tests = [
        "5699176004",  # Электровелосипед
        "2198727557",  # iPhone 15
    ]
    for oid in oz_tests:
        url = f"https://www.ozon.ru/product/{oid}/"
        print(f"\n  [{oid}] {url}")
        try:
            r = os_s.get(url, timeout=(10, 30), headers=hdrs_os)
            print(f"    HTTP {r.status_code}, {len(r.content)}B, final={r.url[:80]}")
            entry = {"http": r.status_code, "bytes": len(r.content), "final": r.url}
            if r.status_code == 200 and len(r.text) > 2000:
                html = r.text
                # Check for __STATE__ / __NEXT_DATA__ / data-widget
                state_m = re.search(r'window\.__STATE__\s*=\s*(\{)', html)
                next_m = re.search(r'__NEXT_DATA__', html)
                dw = re.search(r'data-widget="[^"]*"', html)
                entry["has_state"] = bool(state_m)
                entry["has_next"] = bool(next_m)
                entry["has_dw"] = bool(dw)
                # og
                og_t = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
                entry["og_title"] = og_t.group(1)[:100] if og_t else None
                # JSON-LD
                ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
                entry["jsonld_count"] = len(ld_blocks)
                for lb in ld_blocks[:3]:
                    try:
                        ld = json.loads(lb.strip())
                        if isinstance(ld, dict):
                            t = ld.get("@type", "")
                            if t in ("Product", "IndividualProduct"):
                                entry["jsonld_type"] = t
                                entry["jsonld_name"] = ld.get("name", "")[:100]
                                offers = ld.get("offers", {})
                                if isinstance(offers, dict):
                                    entry["jsonld_price"] = offers.get("price", "")
                                    entry["jsonld_currency"] = offers.get("priceCurrency", "")
                                elif isinstance(offers, list) and offers:
                                    entry["jsonld_price"] = offers[0].get("price", "")
                    except:
                        pass
                # price in HTML
                price_html = re.findall(r'(\d[\d\s]{2,12})\s*(?:₽|руб)', html[:80000])
                entry["price_html"] = price_html[:8]
                # title
                tm = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
                entry["title"] = tm.group(1).strip()[:100] if tm else None
                print(f"    title: {entry.get('title')}")
                print(f"    og: {entry.get('og_title')}")
                print(f"    jsonld: {entry.get('jsonld_type')} / {entry.get('jsonld_name','')[:60]}")
                print(f"    prices: {entry.get('price_html')}")
            elif r.status_code == 403:
                entry["note"] = "403"
                # Check body for challenge type
                body = r.text[:500]
                entry["body_sample"] = body
                entry["has_rr"] = "__rr" in r.url
                entry["has_altcha"] = bool(re.search(r'altcha|challenge|captcha', r.text[:5000], re.I))
                print(f"    403, rr={entry['has_rr']}, altcha={entry['has_altcha']}")
            report[f"ozon_{oid}"] = entry
        except Exception as e:
            print(f"    EXC: {e}")
            report[f"ozon_{oid}"] = {"exc": str(e)}
        time.sleep(3)

    # Step 3: Try composer-api (the real internal API)
    print("\n  Step 3: composer-api")
    try:
        api_url = "https://www.ozon.ru/api/composer-api.bx/page/json/v2"
        params = {"url": "/product/5699176004/"}
        r_api = os_s.get(api_url, params=params, timeout=(10, 25), headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
        })
        print(f"    HTTP {r_api.status_code}, {len(r_api.content)}B")
        entry_api = {"http": r_api.status_code, "bytes": len(r_api.content)}
        if r_api.status_code == 200:
            try:
                data = r_api.json()
                entry_api["top_keys"] = list(data.keys())[:15]
                # Search for price in the JSON
                text = json.dumps(data, ensure_ascii=False)
                prices = re.findall(r'"(?:price|Price)"\s*:\s*"?([\d.]+)"?', text)
                entry_api["prices"] = prices[:10]
                # Find product block
                if "widgetStates" in data:
                    ws = data["widgetStates"]
                    entry_api["widget_count"] = len(ws)
                    # Look for product-related widgets
                    prod_keys = [k for k in ws.keys() if any(x in k.lower() for x in ("product", "price", "offer", "card"))]
                    entry_api["prod_widgets"] = prod_keys[:10]
                    for pk in prod_keys[:3]:
                        wdata = ws[pk]
                        if isinstance(wdata, dict):
                            entry_api[f"widget_{pk[:30]}"] = str(wdata)[:300]
            except:
                entry_api["body200"] = r_api.text[:500]
        report["ozon_composer_api"] = entry_api
        print(f"    keys: {entry_api.get('top_keys')}")
        print(f"    prices: {entry_api.get('prices')}")
        print(f"    prod_widgets: {entry_api.get('prod_widgets')}")
    except Exception as e:
        print(f"    EXC: {e}")
        report["ozon_composer_api"] = {"exc": str(e)}

except Exception as e:
    import traceback; traceback.print_exc()
    report["ozon_error"] = str(e)

# ============================================================
# 3. AVITO — category page with delay
# ============================================================
print("\n" + "=" * 60)
print("3. AVITO (category + delay)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    av_s = cffi_req.Session(impersonate="chrome124")
    hdrs_av = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    # Step 1: Homepage for cookies
    print("  Step 1: homepage")
    r_h = av_s.get("https://www.avito.ru/", timeout=(10, 25), headers=hdrs_av)
    print(f"    HTTP {r_h.status_code}, {len(r_h.content)}B")
    print(f"    Cookies: {list(av_s.cookies.keys())[:10]}")
    time.sleep(5)

    # Step 2: Category page (less protected than search)
    cat_urls = [
        "https://www.avito.ru/vladivostok/auto",
        "https://www.avito.ru/vladivostok/elektronika",
        "https://www.avito.ru/vladivostok/nedvizhimost/prodazha-kvartir",
    ]
    av_ids = []
    for cu in cat_urls:
        print(f"\n  Category: {cu}")
        try:
            r = av_s.get(cu, timeout=(10, 30), headers=hdrs_av)
            print(f"    HTTP {r.status_code}, {len(r.content)}B")
            if r.status_code == 200:
                found = re.findall(r'/ad/(\d{8,})', r.text)
                new = [i for i in found if i not in av_ids]
                av_ids.extend(new)
                print(f"    IDs: {len(found)} found, {len(new)} new")
                if len(av_ids) >= 3:
                    break
            elif r.status_code in (429, 439):
                print(f"    BLOCKED {r.status_code}")
                # Check for captcha
                cap = re.search(r'(altcha|captcha|challenge|pow)', r.text[:5000], re.I)
                print(f"    captcha: {cap.group(0) if cap else 'none'}")
        except Exception as e:
            print(f"    EXC: {e}")
        time.sleep(5)

    report["av_category"] = {"urls": len(cat_urls), "ids": av_ids[:10]}
    print(f"\n  Total AVITO IDs: {len(av_ids)}")

    # Step 3: Test first 2
    for aid in av_ids[:2]:
        aurl = f"https://www.avito.ru/ad/{aid}"
        print(f"\n  Ad {aid}: {aurl}")
        try:
            r2 = av_s.get(aurl, timeout=(10, 30), headers=hdrs_av)
            print(f"    HTTP {r2.status_code}, {len(r2.content)}B")
            entry = {"http": r2.status_code, "bytes": len(r2.content)}
            if r2.status_code == 200 and len(r2.text) > 2000:
                html = r2.text
                tm = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
                entry["title"] = tm.group(1).strip()[:100] if tm else None
                # JSON-LD
                ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
                entry["jsonld_count"] = len(ld_blocks)
                for lb in ld_blocks[:3]:
                    try:
                        ld = json.loads(lb.strip())
                        if isinstance(ld, dict):
                            t = ld.get("@type", "")
                            if t in ("Product", "IndividualProduct", "Offer", "CreativeWork"):
                                entry["jsonld_type"] = t
                                entry["jsonld_name"] = ld.get("name", "")[:100]
                                offers = ld.get("offers", {})
                                if isinstance(offers, dict):
                                    entry["jsonld_price"] = offers.get("price", "")
                                    entry["jsonld_currency"] = offers.get("priceCurrency", "")
                                elif isinstance(offers, list) and offers:
                                    entry["jsonld_price"] = offers[0].get("price", "")
                    except:
                        pass
                # __PRELOADED_STATE__
                ps = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{)', html)
                entry["has_preloaded"] = bool(ps)
                # og
                og_t = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
                entry["og_title"] = og_t.group(1)[:100] if og_t else None
                # price
                price_html = re.findall(r'(\d[\d\s]{2,10})\s*(?:₽|руб)', html[:60000])
                entry["price_html"] = price_html[:8]
                # captcha
                entry["captcha"] = bool(re.search(r'(altcha|captcha|challenge|pow)', html[:5000], re.I))
                print(f"    title: {entry.get('title')}")
                print(f"    jsonld: {entry.get('jsonld_type')} / {entry.get('jsonld_name','')[:60]}")
                print(f"    prices: {entry.get('price_html')}")
                print(f"    captcha: {entry.get('captcha')}")
            elif r2.status_code == 404:
                entry["note"] = "404"
            elif r2.status_code in (403, 429, 439):
                entry["note"] = f"blocked {r2.status_code}"
            report[f"av_{aid}"] = entry
        except Exception as e:
            print(f"    EXC: {e}")
            report[f"av_{aid}"] = {"exc": str(e)}
        time.sleep(4)

except Exception as e:
    import traceback; traceback.print_exc()
    report["avito_error"] = str(e)

# ============================================================
# 4. WB — price from card.wb.ru with basket session
# ============================================================
print("\n" + "=" * 60)
print("4. WB (price endpoints)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    wb_s = cffi_req.Session(impersonate="chrome124")
    nm_id = 199298871
    vol = nm_id // 100000
    part = nm_id // 1000
    host = "basket-13.wbbasket.ru"

    # First: get card.json to establish session
    card_url = f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    r_card = wb_s.get(card_url, timeout=15, headers={"Accept": "application/json"})
    print(f"  card.json: HTTP {r_card.status_code}, {len(r_card.content)}B")
    time.sleep(1)

    # Try multiple price endpoints
    price_endpoints = [
        (f"https://card.wb.ru/cards/v2/detail?nm={nm_id}", "card_v2_detail"),
        (f"https://card.wb.ru/cards/v1/detail?nm={nm_id}", "card_v1_detail"),
        (f"https://wbx-content-v2.wbstatic.net/card/price/{nm_id}.json", "wbx_price"),
        (f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/price.json", "basket_price"),
        (f"https://app.wildberries.ru/webapi/common/v1/nm/{nm_id}/price", "webapi_price"),
    ]
    wb_entry = {}
    for purl, label in price_endpoints:
        print(f"\n  [{label}] {purl}")
        try:
            r_p = wb_s.get(purl, timeout=(8, 15), headers={
                "Accept": "application/json",
                "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                "Origin": "https://www.wildberries.ru",
            })
            print(f"    HTTP {r_p.status_code}, {len(r_p.content)}B")
            pe = {"http": r_p.status_code, "bytes": len(r_p.content)}
            if r_p.status_code == 200 and len(r_p.content) > 10:
                try:
                    pd = r_p.json()
                    pe["keys"] = list(pd.keys())[:15] if isinstance(pd, dict) else str(pd)[:200]
                    # Look for price
                    text = json.dumps(pd, ensure_ascii=False)
                    prices = re.findall(r'"(?:price|basePrice|salePrice|priceU|priceSale)"\s*:\s*"?([\d.]+)"?', text)
                    pe["prices"] = prices[:10]
                    # If it's a list of cards
                    if isinstance(pd, list) and pd:
                        pe["first_item_keys"] = list(pd[0].keys())[:20] if isinstance(pd[0], dict) else str(pd[0])[:100]
                        # Check for price in first item
                        item_text = json.dumps(pd[0], ensure_ascii=False)
                        item_prices = re.findall(r'"(?:price|basePrice|salePrice|priceU)"\s*:\s*"?([\d.]+)"?', item_text)
                        pe["item_prices"] = item_prices[:10]
                    print(f"    keys: {pe.get('keys')}")
                    print(f"    prices: {pe.get('prices')}")
                except:
                    pe["body200"] = r_p.text[:300]
            elif r_p.status_code == 404:
                pe["note"] = "404"
            elif r_p.status_code in (403, 498):
                pe["note"] = f"blocked {r_p.status_code}"
            wb_entry[label] = pe
        except Exception as e:
            print(f"    EXC: {e}")
            wb_entry[label] = {"exc": str(e)}
        time.sleep(1)
    report["wb_price"] = wb_entry

except Exception as e:
    import traceback; traceback.print_exc()
    report["wb_error"] = str(e)

# Save
out = Path(__file__).parent / "diag_v111_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nReport: {out}")
print("DONE")
