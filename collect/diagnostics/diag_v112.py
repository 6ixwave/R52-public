"""Diagnostic v112: Farpost SEO through collector, Ozon mobile+rr, Avito PoW, WB webapi."""
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("diag_v112")

report = {}

# ============================================================
# 1. FARPOST — 3 working SEO URLs through actual parser
# ============================================================
print("=" * 60)
print("1. FARPOST (SEO through collector)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    fp_s = cffi_req.Session(impersonate="chrome124")
    hdrs = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    seo_tests = [
        ("66422428", "https://www.farpost.ru/vladivostok/food/seafood/nerka-pbg-2025-2026-god-raznye-razmery-kamchatka-dostavka-66422428.html"),
        ("134164000", "https://www.farpost.ru/vladivostok/zapchasti/akkumulyator-oursun-din-180-2025-2026-134164000.html"),
        ("136391752", "https://www.farpost.ru/vladivostok/auto/security/farrop-tenet-t8-15800-136391752.html"),
    ]

    for bid, seourl in seo_tests:
        print(f"\n  [{bid}]")
        try:
            r = fp_s.get(seourl, timeout=(10, 25), headers=hdrs)
            print(f"    HTTP {r.status_code}, {len(r.content)}B")
            entry = {"http": r.status_code, "bytes": len(r.content), "url": seourl}
            if r.status_code == 200 and len(r.text) > 2000:
                html = r.text
                # Title
                tm = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
                entry["title"] = tm.group(1).strip()[:120] if tm else None
                # JSON-LD full extraction
                ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
                for lb in ld_blocks:
                    try:
                        ld = json.loads(lb.strip())
                        if isinstance(ld, dict) and ld.get("@type") == "Product":
                            entry["jsonld"] = {
                                "name": ld.get("name", "")[:120],
                                "description": str(ld.get("description", ""))[:200],
                                "image": ld.get("image", [])[:3] if isinstance(ld.get("image"), list) else [ld.get("image", "")],
                                "offers": ld.get("offers", {}),
                                "sku": ld.get("sku", ""),
                                "brand": str(ld.get("brand", ""))[:80],
                            }
                            break
                    except:
                        pass
                # data-bulletin-price
                bp = re.search(r'data-bulletin-price="([^"]*)"', html)
                entry["data_price"] = bp.group(1) if bp else None
                # Gallery
                gal = re.findall(r'data-image-info="([^"]*)"', html)
                entry["gallery_count"] = len(gal)
                if gal:
                    entry["gallery_sample"] = gal[:2]
                # Seller
                seller_m = re.search(r'class="[^"]*seller[^"]*"[^>]*>(.*?)</', html, re.DOTALL)
                entry["seller"] = re.sub(r'<[^>]+>', '', seller_m.group(1)).strip()[:80] if seller_m else None
                # Specs / options
                specs = re.findall(r'class="[^"]*bulletin-prop[^"]*"[^>]*>.*?class="[^"]*prop-name[^"]*"[^>]*>(.*?)</.*?class="[^"]*prop-value[^"]*"[^>]*>(.*?)</', html, re.DOTALL)
                entry["specs_count"] = len(specs)
                if specs:
                    entry["specs_sample"] = [(re.sub(r'<[^>]+>','',s[0]).strip()[:40], re.sub(r'<[^>]+>','',s[1]).strip()[:40]) for s in specs[:5]]
                # Category breadcrumb
                bc = re.findall(r'class="[^"]*breadcrumb[^"]*".*?</nav>', html, re.DOTALL)
                if bc:
                    cats = re.findall(r'>([^<]{3,40})</a>', bc[0])
                    entry["breadcrumb"] = cats[:6]

                print(f"    title: {entry.get('title','')[:80]}")
                print(f"    price: {entry.get('data_price')}")
                print(f"    jsonld offers: {json.dumps(entry.get('jsonld',{}).get('offers',{}), ensure_ascii=False)[:150]}")
                print(f"    gallery: {entry.get('gallery_count')}")
                print(f"    specs: {entry.get('specs_count')}")
            report[f"fp_{bid}"] = entry
        except Exception as e:
            print(f"    EXC: {e}")
            report[f"fp_{bid}"] = {"exc": str(e)}
        time.sleep(2)

    report["fp_summary"] = {
        "tested": len(seo_tests),
        "ok": sum(1 for k,v in report.items() if k.startswith("fp_") and v.get("http")==200),
    }
except Exception as e:
    import traceback; traceback.print_exc()
    report["farpost_error"] = str(e)

# ============================================================
# 2. OZON — mobile UA + __rr redirect
# ============================================================
print("\n" + "=" * 60)
print("2. OZON (mobile + __rr)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    # Approach A: mobile UA
    os_m = cffi_req.Session(impersonate="chrome124")
    mob_hdrs = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    }
    # Warmup
    r_h = os_m.get("https://www.ozon.ru/", timeout=(10, 20), headers=mob_hdrs)
    print(f"  Mobile home: HTTP {r_h.status_code}, {len(r_h.content)}B")
    time.sleep(3)

    oz_tests = ["5699176004", "2198727557"]
    for oid in oz_tests:
        # Try with __rr=1
        url = f"https://www.ozon.ru/product/{oid}/?__rr=1"
        print(f"\n  [{oid}] mobile+rr: {url}")
        try:
            r = os_m.get(url, timeout=(10, 30), headers=mob_hdrs, allow_redirects=True)
            print(f"    HTTP {r.status_code}, {len(r.content)}B, final={r.url[:80]}")
            entry = {"http": r.status_code, "bytes": len(r.content), "final": r.url}
            if r.status_code == 200 and len(r.text) > 2000:
                html = r.text
                tm = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
                entry["title"] = tm.group(1).strip()[:100] if tm else None
                # JSON-LD
                ld_blocks = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
                for lb in ld_blocks[:3]:
                    try:
                        ld = json.loads(lb.strip())
                        if isinstance(ld, dict) and ld.get("@type") in ("Product","IndividualProduct"):
                            entry["jsonld_name"] = ld.get("name","")[:100]
                            entry["jsonld_price"] = str(ld.get("offers",{}).get("price",""))[:50]
                            break
                    except:
                        pass
                # og
                og_t = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
                entry["og_title"] = og_t.group(1)[:100] if og_t else None
                # price
                prices = re.findall(r'(\d[\d\s]{2,12})\s*(?:₽|руб)', html[:80000])
                entry["prices"] = prices[:5]
                # __STATE__
                entry["has_state"] = bool(re.search(r'window\.__STATE__', html))
                print(f"    title: {entry.get('title')}")
                print(f"    jsonld: {entry.get('jsonld_name','')[:60]} / {entry.get('jsonld_price')}")
                print(f"    prices: {entry.get('prices')}")
            elif r.status_code == 403:
                entry["note"] = "403"
                entry["altcha"] = bool(re.search(r'altcha|challenge', r.text[:5000], re.I))
                # Try to extract challenge
                ch = re.search(r'data-challenge="([^"]*)"', r.text)
                entry["challenge"] = ch.group(1)[:100] if ch else None
                print(f"    403, altcha={entry['altcha']}, challenge={entry.get('challenge')}")
            report[f"ozon_mob_{oid}"] = entry
        except Exception as e:
            print(f"    EXC: {e}")
            report[f"ozon_mob_{oid}"] = {"exc": str(e)}
        time.sleep(3)

    # Approach B: desktop with __rr
    os_d = cffi_req.Session(impersonate="chrome124")
    d_hdrs = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    r_h2 = os_d.get("https://www.ozon.ru/", timeout=(10, 20), headers=d_hdrs)
    print(f"\n  Desktop home: HTTP {r_h2.status_code}, {len(r_h2.content)}B")
    time.sleep(3)
    oid = "5699176004"
    url = f"https://www.ozon.ru/product/{oid}/?__rr=1"
    print(f"  [{oid}] desktop+rr")
    try:
        r = os_d.get(url, timeout=(10, 30), headers=d_hdrs, allow_redirects=True)
        print(f"    HTTP {r.status_code}, {len(r.content)}B, final={r.url[:80]}")
        entry = {"http": r.status_code, "bytes": len(r.content), "final": r.url}
        if r.status_code == 200 and len(r.text) > 2000:
            html = r.text
            tm = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
            entry["title"] = tm.group(1).strip()[:100] if tm else None
            prices = re.findall(r'(\d[\d\s]{2,12})\s*(?:₽|руб)', html[:80000])
            entry["prices"] = prices[:5]
            entry["has_state"] = bool(re.search(r'window\.__STATE__', html))
            print(f"    title: {entry.get('title')}")
            print(f"    prices: {entry.get('prices')}")
        elif r.status_code == 403:
            entry["note"] = "403"
            entry["altcha"] = bool(re.search(r'altcha|challenge', r.text[:5000], re.I))
        report[f"ozon_desk_{oid}"] = entry
    except Exception as e:
        print(f"    EXC: {e}")
        report[f"ozon_desk_{oid}"] = {"exc": str(e)}

except Exception as e:
    import traceback; traceback.print_exc()
    report["ozon_error"] = str(e)

# ============================================================
# 3. AVITO — PoW challenge solve
# ============================================================
print("\n" + "=" * 60)
print("3. AVITO (PoW solve)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    import hashlib
    av_s = cffi_req.Session(impersonate="chrome124")
    hdrs_av = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    # Step 1: Get challenge
    print("  Step 1: homepage for PoW challenge")
    r = av_s.get("https://www.avito.ru/", timeout=(10, 25), headers=hdrs_av)
    print(f"    HTTP {r.status_code}, {len(r.content)}B")
    print(f"    Cookies: {list(av_s.cookies.keys())}")

    # Extract PoW parameters from HTML
    html = r.text
    # Look for challenge in script or meta
    pow_script = re.search(r'pow_challenge.*?(\d{4,8})', html, re.DOTALL)
    pow_diff = re.search(r'(?:difficulty|diff|prefix)\s*[:=]\s*["\']?([^"\',\s]+)', html, re.I)
    pow_nonce_max = re.search(r'(?:maxNonce|nonceMax|nonce_max)\s*[:=]\s*(\d+)', html, re.I)
    pow_salt = re.search(r'(?:salt|seed)\s*[:=]\s*["\']([^"\']+)["\']', html, re.I)

    # Also check for the challenge in a specific script block
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    pow_params = {}
    for s in scripts:
        if 'pow' in s.lower() or 'challenge' in s.lower() or 'nonce' in s.lower():
            pow_params["script_len"] = len(s)
            pow_params["script_sample"] = s[:500]
            # Try to extract params
            for pat in [r'challenge["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                       r'difficulty["\']?\s*[:=]\s*["\']?(\d+)',
                       r'salt["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                       r'prefix["\']?\s*[:=]\s*["\']([^"\']+)["\']']:
                m = re.search(pat, s)
                if m:
                    pow_params[pat[:20]] = m.group(1)
            break

    entry_pow = {"http": r.status_code, "cookies": list(av_s.cookies.keys()), "pow_params": pow_params}
    print(f"    PoW params: {pow_params}")

    # Step 2: Try to solve PoW (simple: find nonce where sha256(salt+nonce) starts with prefix)
    if pow_params:
        salt = pow_params.get("salt['", "") or pow_params.get("salt", "")
        prefix = pow_params.get("prefix['", "") or pow_params.get("prefix", "")
        if not salt:
            salt = "avito"
        if not prefix:
            prefix = "00"

        print(f"  Step 2: solving PoW (salt={salt[:20]}, prefix={prefix})")
        solved = False
        for nonce in range(1000000):
            h = hashlib.sha256(f"{salt}{nonce}".encode()).hexdigest()
            if h.startswith(prefix):
                entry_pow["nonce"] = nonce
                entry_pow["hash"] = h
                print(f"    SOLVED: nonce={nonce}, hash={h[:30]}...")
                solved = True
                break
        if not solved:
            entry_pow["note"] = "not solved in 1M"
            print(f"    Not solved in 1M attempts")

    # Step 3: Try category page after warmup (even without PoW solved)
    time.sleep(5)
    cat_url = "https://www.avito.ru/vladivostok/elektronika/telefony"
    print(f"\n  Step 3: category {cat_url}")
    try:
        r2 = av_s.get(cat_url, timeout=(10, 30), headers=hdrs_av)
        print(f"    HTTP {r2.status_code}, {len(r2.content)}B")
        entry_cat = {"http": r2.status_code, "bytes": len(r2.content)}
        if r2.status_code == 200:
            av_ids = re.findall(r'/ad/(\d{8,})', r2.text)
            av_ids = list(dict.fromkeys(av_ids))[:5]
            entry_cat["ids"] = av_ids
            print(f"    IDs: {av_ids}")
        elif r2.status_code in (429, 439):
            entry_cat["note"] = f"blocked {r2.status_code}"
        report["av_category"] = entry_cat
    except Exception as e:
        print(f"    EXC: {e}")
        report["av_category"] = {"exc": str(e)}

    # Step 4: Test a known ad ID (from search results)
    # Avito ads are typically 10-11 digits
    test_ads = ["1165432100", "1170000000", "1180000000"]
    for aid in test_ads:
        aurl = f"https://www.avito.ru/ad/{aid}"
        print(f"\n  Ad {aid}")
        try:
            r3 = av_s.get(aurl, timeout=(10, 25), headers=hdrs_av)
            print(f"    HTTP {r3.status_code}, {len(r3.content)}B")
            entry_ad = {"http": r3.status_code, "bytes": len(r3.content)}
            if r3.status_code == 200 and len(r3.text) > 2000:
                html3 = r3.text
                tm = re.search(r'<title>(.*?)</title>', html3, re.DOTALL)
                entry_ad["title"] = tm.group(1).strip()[:100] if tm else None
                prices = re.findall(r'(\d[\d\s]{2,10})\s*(?:₽|руб)', html3[:60000])
                entry_ad["prices"] = prices[:5]
                entry_ad["captcha"] = bool(re.search(r'altcha|captcha|challenge', html3[:5000], re.I))
                print(f"    title: {entry_ad.get('title')}")
                print(f"    prices: {entry_ad.get('prices')}")
            elif r3.status_code == 404:
                entry_ad["note"] = "404"
            report[f"av_{aid}"] = entry_ad
        except Exception as e:
            print(f"    EXC: {e}")
            report[f"av_{aid}"] = {"exc": str(e)}
        time.sleep(3)

    report["av_pow"] = entry_pow
except Exception as e:
    import traceback; traceback.print_exc()
    report["avito_error"] = str(e)

# ============================================================
# 4. WB — webapi.wildberries.ru (correct host)
# ============================================================
print("\n" + "=" * 60)
print("4. WB (webapi host)")
print("=" * 60)

try:
    from curl_cffi import requests as cffi_req
    wb_s = cffi_req.Session(impersonate="chrome124")
    nm_id = 199298871

    # Try webapi.wildberries.ru (not app.wildberries.ru)
    wb_urls = [
        (f"https://webapi.wildberries.ru/webapi/common/v1/nm/{nm_id}/price", "webapi_price"),
        (f"https://webapi.wildberries.ru/webapi/common/v1/nm/{nm_id}/card", "webapi_card"),
        (f"https://card.wb.ru/cards/v2/detail?nm={nm_id}", "card_v2"),
        (f"https://card.wb.ru/cards/detail?nm={nm_id}", "card_v1"),
    ]
    wb_entry = {}
    for wurl, label in wb_urls:
        print(f"\n  [{label}] {wurl}")
        try:
            r = wb_s.get(wurl, timeout=(8, 15), headers={
                "Accept": "application/json",
                "Referer": f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                "Origin": "https://www.wildberries.ru",
            })
            print(f"    HTTP {r.status_code}, {len(r.content)}B")
            pe = {"http": r.status_code, "bytes": len(r.content)}
            if r.status_code == 200 and len(r.content) > 10:
                try:
                    pd = r.json()
                    text = json.dumps(pd, ensure_ascii=False)
                    # Find price patterns
                    prices = re.findall(r'"(?:price|basePrice|salePrice|priceU|priceSale|priceTotal)"\s*:\s*"?([\d.]+)"?', text)
                    pe["prices"] = prices[:10]
                    pe["keys"] = list(pd.keys())[:15] if isinstance(pd, dict) else (list(pd[0].keys())[:15] if isinstance(pd, list) and pd else [])
                    print(f"    keys: {pe.get('keys')[:10]}")
                    print(f"    prices: {pe.get('prices')}")
                except:
                    pe["body"] = r.text[:300]
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
out = Path(__file__).parent / "diag_v112_report.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nReport: {out}")
print("DONE")
