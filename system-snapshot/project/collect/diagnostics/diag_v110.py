"""Diagnostic v110: Farpost SEO URLs, Ozon API, Avito real IDs, WB data field."""
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v110")

report = {}

# ============================================================
# 1. FARPOST — SEO URL pattern (slug-ID.html)
# ============================================================
print("=" * 60)
print("1. FARPOST (SEO URLs)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    fp_s = cffi_req.Session(impersonate="chrome124")
    
    # Fetch homepage — extract IDs from SEO pattern
    r = fp_s.get("https://www.farpost.ru/", timeout=(10, 25), headers={
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9",
    })
    print(f"  Homepage: HTTP {r.status_code}, {len(r.content)}B")
    
    # Pattern 1: /bulletin/ID
    ids_bulletin = re.findall(r'/bulletin/(\d{6,})', r.text)
    # Pattern 2: SEO slug-ID.html (at end of path)
    ids_seo = re.findall(r'-(\d{6,})\.html', r.text)
    # Pattern 3: any /ID.html
    ids_any = re.findall(r'/(\d{6,})\.html', r.text)
    
    all_ids = list(dict.fromkeys(ids_bulletin + ids_seo + ids_any))
    print(f"  IDs: bulletin={len(ids_bulletin)}, seo={len(ids_seo)}, any={len(ids_any)}, total_unique={len(all_ids)}")
    print(f"  First 10: {all_ids[:10]}")
    
    # Also check: what do the links look like?
    links = re.findall(r'href="([^"]*farpost[^"]*|[^"]*bulletin[^"]*|[^"]*/\d{6,}[^"]*)"', r.text[:20000])
    print(f"  Sample links: {links[:5]}")
    
    report["fp_homepage"] = {
        "http": r.status_code,
        "ids_bulletin": ids_bulletin[:10],
        "ids_seo": ids_seo[:10],
        "ids_any": ids_any[:10],
        "total": len(all_ids),
    }
    
    # Test 3 live IDs
    test_ids = all_ids[:3]
    if not test_ids:
        # Fallback: use IDs from web search
        test_ids = ["131630486", "66422428", "134164000"]
        print(f"  Fallback IDs from search: {test_ids}")
    
    for bid in test_ids:
        # Try both URL patterns
        urls_to_try = [
            f"https://www.farpost.ru/bulletin/{bid}",
            f"https://www.farpost.ru/vladivostok/bulletin/{bid}",
        ]
        # If SEO ID, also try the SEO path (but we don't know the slug, so bulletin/ should redirect)
        
        best_entry = None
        for burl in urls_to_try:
            try:
                r2 = fp_s.get(burl, timeout=(10, 25), headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "ru-RU,ru;q=0.9",
                })
                print(f"\n  [{bid}] {burl}")
                print(f"    HTTP {r2.status_code}, {len(r2.content)}B, final={r2.url[:80]}")
                entry = {"http": r2.status_code, "bytes": len(r2.content), "final": r2.url, "url": burl}
                
                if r2.status_code == 200 and len(r2.text) > 1000:
                    html = r2.text
                    # JSON-LD
                    ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
                    entry["jsonld_count"] = len(ld_blocks)
                    for lb in ld_blocks[:3]:
                        try:
                            ld = json.loads(lb.strip())
                            if isinstance(ld, dict):
                                t = ld.get("@type", "")
                                entry[f"jsonld_{t}"] = {
                                    "name": ld.get("name", "")[:80],
                                    "offers": str(ld.get("offers", ""))[:200],
                                }
                                if t in ("Product", "IndividualProduct", "Offer"):
                                    entry["jsonld_type"] = t
                                    entry["jsonld_name"] = ld.get("name", "")[:80]
                                    offers = ld.get("offers", {})
                                    if isinstance(offers, dict):
                                        entry["jsonld_price"] = offers.get("price", offers.get("lowPrice", ""))
                                        entry["jsonld_currency"] = offers.get("priceCurrency", "")
                                    elif isinstance(offers, list) and offers:
                                        entry["jsonld_price"] = offers[0].get("price", "")
                        except Exception as e:
                            entry[f"jsonld_err"] = str(e)
                    # data-bulletin-price
                    bp = re.search(r'data-bulletin-price="([^"]*)"', html)
                    entry["data_price"] = bp.group(1) if bp else None
                    # og
                    og_t = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
                    entry["og_title"] = og_t.group(1)[:80] if og_t else None
                    # price in visible text
                    price_html = re.findall(r'(\d[\d\s]{2,10})\s*(?:₽|руб)', html[:50000])
                    entry["price_html"] = price_html[:5]
                    # baza.lightbox
                    lb_init = re.search(r'baza\.lightbox\.init\((\[.*?\])\)', html, re.DOTALL)
                    entry["has_lightbox"] = bool(lb_init)
                    # title tag
                    title_m = re.search(r'<title>(.*?)</title>', html)
                    entry["title"] = title_m.group(1)[:80] if title_m else None
                    
                    print(f"    title: {entry.get('title')}")
                    print(f"    jsonld: {entry.get('jsonld_type')} / {entry.get('jsonld_name','')[:50]}")
                    print(f"    data_price: {entry.get('data_price')}")
                    print(f"    price_html: {entry.get('price_html')}")
                    best_entry = entry
                elif r2.status_code == 404:
                    entry["note"] = "404"
                    print(f"    404")
                best_entry = entry
                report[f"fp_{bid}"] = entry
            except Exception as e:
                print(f"    EXC: {e}")
                report[f"fp_{bid}"] = {"exc": str(e)}
            time.sleep(2)
except Exception as e:
    import traceback; traceback.print_exc()
    report["farpost_error"] = str(e)

# ============================================================
# 2. OZON — try API + different approaches
# ============================================================
print("\n" + "=" * 60)
print("2. OZON (API + approaches)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    os_s = cffi_req.Session(impersonate="chrome124")
    
    # Approach A: entrypoint-api (internal JSON API)
    print("\n  A: entrypoint-api")
    api_url = "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url=/product/5699176004/"
    r = os_s.get(api_url, timeout=(10, 25), headers={
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
    })
    print(f"    HTTP {r.status_code}, {len(r.content)}B")
    entry_a = {"http": r.status_code, "bytes": len(r.content)}
    if r.status_code == 200:
        try:
            data = r.json()
            entry_a["keys"] = list(data.keys())[:20]
            # Look for price
            text = json.dumps(data, ensure_ascii=False)
            prices = re.findall(r'"(?:price|Price|PRICE)"\s*:\s*"?([\d.]+)"?', text)
            entry_a["prices_found"] = prices[:10]
            print(f"    keys: {list(data.keys())[:15]}")
            print(f"    prices: {prices[:5]}")
        except:
            entry_a["body200"] = r.text[:300]
    report["ozon_api"] = entry_a
    
    time.sleep(2)
    
    # Approach B: mobile API
    print("\n  B: mobile endpoint")
    mob_url = "https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/5699176004/"
    r2 = os_s.get(mob_url, timeout=(10, 25), headers={
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
    })
    print(f"    HTTP {r2.status_code}, {len(r2.content)}B")
    entry_b = {"http": r2.status_code, "bytes": len(r2.content)}
    if r2.status_code == 200:
        try:
            data2 = r2.json()
            entry_b["keys"] = list(data2.keys())[:20]
        except:
            entry_b["body200"] = r2.text[:300]
    report["ozon_mobile"] = entry_b
    
    time.sleep(2)
    
    # Approach C: direct product page with full browser headers
    print("\n  C: full browser headers")
    prod_url = "https://www.ozon.ru/product/5699176004/"
    r3 = os_s.get(prod_url, timeout=(10, 30), headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    })
    print(f"    HTTP {r3.status_code}, {len(r3.content)}B, final={r3.url[:80]}")
    entry_c = {"http": r3.status_code, "bytes": len(r3.content), "final": r3.url}
    if r3.status_code == 200 and len(r3.text) > 2000:
        html = r3.text
        # Check for __STATE__ or __NEXT_DATA__
        state_m = re.search(r'window\.__STATE__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
        next_m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        entry_c["has_state"] = bool(state_m)
        entry_c["has_next_data"] = bool(next_m)
        # og
        og_t = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
        entry_c["og_title"] = og_t.group(1)[:80] if og_t else None
        # price
        price_html = re.findall(r'(\d[\d\s]{2,12})\s*(?:₽|руб)', html[:80000])
        entry_c["price_html"] = price_html[:5]
        # JSON-LD
        ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        entry_c["jsonld_count"] = len(ld_blocks)
        for lb in ld_blocks[:2]:
            try:
                ld = json.loads(lb.strip())
                if isinstance(ld, dict):
                    entry_c["jsonld_type"] = ld.get("@type", "")
                    entry_c["jsonld_name"] = ld.get("name", "")[:80]
                    offers = ld.get("offers", {})
                    if isinstance(offers, dict):
                        entry_c["jsonld_price"] = offers.get("price", "")
                    elif isinstance(offers, list) and offers:
                        entry_c["jsonld_price"] = offers[0].get("price", "")
            except:
                pass
        print(f"    og: {entry_c.get('og_title')}")
        print(f"    state: {entry_c.get('has_state')}, next: {entry_c.get('has_next_data')}")
        print(f"    prices: {entry_c.get('price_html')}")
        print(f"    jsonld: {entry_c.get('jsonld_type')} / {entry_c.get('jsonld_name','')[:50]}")
    elif r3.status_code == 403:
        entry_c["note"] = "still_403"
        entry_c["body200"] = r3.text[:300]
        print(f"    Still 403")
    report["ozon_browser"] = entry_c
except Exception as e:
    import traceback; traceback.print_exc()
    report["ozon_error"] = str(e)

# ============================================================
# 3. AVITO — real IDs from search, curl_cffi
# ============================================================
print("\n" + "=" * 60)
print("3. AVITO (real IDs)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    av_s = cffi_req.Session(impersonate="chrome124")
    
    # Try search with different paths
    av_search_urls = [
        "https://www.avito.ru/vladivostok/all?q=%D0%B1%D0%B5%D0%BD%D0%B7&sort=price_desc",
        "https://www.avito.ru/all?q=%D0%B1%D0%B5%D0%BD%D0%B7&sort=price_desc",
        "https://www.avito.ru/vladivostok/auto",
    ]
    av_ids = []
    for su in av_search_urls:
        print(f"\n  Search: {su}")
        try:
            r = av_s.get(su, timeout=(10, 30), headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
            })
            print(f"    HTTP {r.status_code}, {len(r.content)}B")
            if r.status_code == 200:
                found = re.findall(r'/ad/(\d{8,})', r.text)
                new = [i for i in found if i not in av_ids]
                av_ids.extend(new)
                print(f"    IDs: {len(found)} found, {len(new)} new")
                if len(av_ids) >= 3:
                    break
            elif r.status_code == 439:
                print(f"    BOT CHALLENGE (439)")
                # Check for captcha marker
                cap = re.search(r'(altcha|captcha|challenge)', r.text[:5000], re.I)
                print(f"    captcha marker: {cap.group(0) if cap else 'none'}")
        except Exception as e:
            print(f"    EXC: {e}")
        time.sleep(2)
    
    report["av_search"] = {"urls": len(av_search_urls), "ids": av_ids[:10]}
    
    # If no IDs from search, use known patterns
    if not av_ids:
        # Avito IDs in 2025-2026 are typically 10-12 digits starting with 1
        av_ids = ["1165432100", "1172345600", "1180123400"]
        print(f"  Fallback IDs: {av_ids}")
    
    # Test first 2
    for aid in av_ids[:2]:
        aurl = f"https://www.avito.ru/ad/{aid}"
        print(f"\n  Ad {aid}: {aurl}")
        try:
            r2 = av_s.get(aurl, timeout=(10, 30), headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
                "Referer": "https://www.avito.ru/",
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
                        ld = json.loads(lb.strip())
                        if isinstance(ld, dict):
                            t = ld.get("@type", "")
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
                # captcha
                entry["captcha"] = bool(re.search(r'(altcha|captcha|challenge)', html[:5000], re.I))
                # title
                title_m = re.search(r'<title>(.*?)</title>', html)
                entry["title"] = title_m.group(1)[:80] if title_m else None
                print(f"    title: {entry.get('title')}")
                print(f"    jsonld: {entry.get('jsonld_type')} / {entry.get('jsonld_name','')[:50]}")
                print(f"    prices: {entry.get('price_html')}")
                print(f"    captcha: {entry.get('captcha')}")
            elif r2.status_code == 404:
                entry["note"] = "404 (ID not found)"
            elif r2.status_code in (403, 429, 439):
                entry["note"] = f"blocked {r2.status_code}"
            report[f"av_{aid}"] = entry
        except Exception as e:
            print(f"    EXC: {e}")
            report[f"av_{aid}"] = {"exc": str(e)}
        time.sleep(3)
except Exception as e:
    import traceback; traceback.print_exc()
    report["avito_error"] = str(e)

# ============================================================
# 4. WB — check 'data' and 'grouped_options' for price
# ============================================================
print("\n" + "=" * 60)
print("4. WB (data + grouped_options)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    s = cffi_req.Session(impersonate="chrome124")
    nm_id = 199298871
    vol = nm_id // 100000
    part = nm_id // 1000
    host = "basket-13.wbbasket.ru"
    card_url = f"https://{host}/vol{vol}/part{part}/{nm_id}/info/ru/card.json"
    resp = s.get(card_url, timeout=15)
    print(f"  card.json: HTTP {resp.status_code}, {len(resp.content)}B")
    wb_entry = {"http": resp.status_code, "bytes": len(resp.content)}
    if resp.status_code == 200:
        data = resp.json()
        # 'data' field
        d = data.get("data", {})
        wb_entry["data_keys"] = list(d.keys())[:30] if isinstance(d, dict) else str(d)[:200]
        if isinstance(d, dict):
            # Look for price in data
            for k, v in d.items():
                if any(x in k.lower() for x in ("price", "sell", "cost", "amount", "rub", "sale", "discount")):
                    wb_entry[f"data_{k}"] = str(v)[:200]
        # 'grouped_options'
        go = data.get("grouped_options", [])
        wb_entry["grouped_options_count"] = len(go) if isinstance(go, list) else 0
        if isinstance(go, list):
            for g in go[:10]:
                if isinstance(g, dict):
                    gname = g.get("name", "")
                    wb_entry[f"go_{gname}"] = str(g.get("values", g.get("value", "")))[:200]
        # 'properties'
        props = data.get("properties", [])
        wb_entry["properties_count"] = len(props) if isinstance(props, list) else 0
        if isinstance(props, list):
            for p in props[:20]:
                if isinstance(p, dict):
                    pname = p.get("name", "")
                    if any(x in pname.lower() for x in ("цена", "price", "стоимост", "sale")):
                        wb_entry[f"prop_{pname}"] = str(p.get("value", ""))[:200]
        # 'compositions'
        comp = data.get("compositions", [])
        wb_entry["compositions"] = str(comp)[:300] if comp else "empty"
        
        print(f"  data keys: {wb_entry['data_keys']}")
        print(f"  grouped_options: {wb_entry['grouped_options_count']}")
        print(f"  properties: {wb_entry['properties_count']}")
        # Print full data field if small
        if isinstance(d, dict) and len(json.dumps(d)) < 2000:
            print(f"  data: {json.dumps(d, ensure_ascii=False)[:500]}")
    report["wb"] = wb_entry
except Exception as e:
    import traceback; traceback.print_exc()
    report["wb_error"] = str(e)

# Save
out = Path(__file__).parent / "diag_v110_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nReport: {out}")
print("DONE")
