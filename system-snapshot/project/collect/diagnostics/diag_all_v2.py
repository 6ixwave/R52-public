"""Диагностика v2: WB + Ozon + Avito — обход анти-бота.

WB: chrome124, Referer, HTTP/1.1, wbx-content с долгим таймаутом.
Ozon: verify=False, entrypoint-api, composer-api.
Avito: задержки, X-Requested-With, api path.
"""
import json, sys, time, re, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
OUT = Path(__file__).parent

WB_IDS = ["199298871", "314337853"]
OZON_IDS = ["3349505344", "2718589061"]
AVITO_IDS = ["7345810001", "7201234567"]

results = {"WB": [], "OZON": [], "AVITO": []}

from curl_cffi import requests as cr

# ============================================================
# WB
# ============================================================
print("=" * 70)
print("WB: chrome124 + Referer + HTTP/1.1 + wbx-content")
print("=" * 70)

# --- WB 1: card.wb.ru с Referer и chrome124 ---
for imp in ("chrome124", "chrome120", "chrome116"):
    for wid in WB_IDS[:1]:
        label = f"WB card {imp} {wid}"
        print(f"\n[{label}]")
        try:
            r = cr.get(
                "https://card.wb.ru/cards/v2/detail",
                params={"nm": wid},
                impersonate=imp,
                timeout=15,
                headers={
                    "Referer": "https://www.wildberries.ru/",
                    "Origin": "https://www.wildberries.ru",
                    "Accept": "application/json, text/plain, */*",
                },
            )
            print(f"  {r.status_code} | {len(r.content)}B")
            if r.status_code == 200:
                data = r.json()
                prods = data.get("data", {}).get("products", [])
                print(f"  Products: {len(prods)}")
                if prods:
                    p = prods[0]
                    print(f"  name={p.get('name','?')[:60]} brand={p.get('brand','?')}")
                    results["WB"].append({"id": wid, "method": f"card_{imp}", "status": "OK", "name": p.get("name","")[:60]})
                    break
                else:
                    results["WB"].append({"id": wid, "method": f"card_{imp}", "status": "NO_PRODUCTS"})
            else:
                results["WB"].append({"id": wid, "method": f"card_{imp}", "status": r.status_code})
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {str(e)[:150]}")
            results["WB"].append({"id": wid, "method": f"card_{imp}", "status": str(e)[:80]})
        time.sleep(1.5)

# --- WB 2: HTML с Referer + chrome124 ---
for wid in WB_IDS:
    label = f"WB HTML chrome124 {wid}"
    print(f"\n[{label}]")
    try:
        r = cr.get(
            f"https://www.wildberries.ru/catalog/{wid}/detail.aspx",
            impersonate="chrome124",
            timeout=15,
            headers={
                "Referer": "https://www.wildberries.ru/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
        )
        print(f"  {r.status_code} | {len(r.content)}B")
        if r.status_code == 200:
            text = r.text
            ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.DOTALL)
            print(f"  JSON-LD: {len(ld)} blocks")
            for i, block in enumerate(ld[:3]):
                try:
                    obj = json.loads(block.strip())
                    if isinstance(obj, dict):
                        print(f"    [{i}] @type={obj.get('@type','?')} name={str(obj.get('name',''))[:60]}")
                        offers = obj.get("offers")
                        if offers:
                            print(f"    offers: {json.dumps(offers, ensure_ascii=False)[:200]}")
                except:
                    pass
            results["WB"].append({"id": wid, "method": "html_chrome124", "status": "OK", "len": len(text)})
        else:
            results["WB"].append({"id": wid, "method": "html_chrome124", "status": r.status_code})
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)[:150]}")
        results["WB"].append({"id": wid, "method": "html_chrome124", "status": str(e)[:80]})
    time.sleep(2)

# --- WB 3: wbx-content с долгим таймаутом ---
for wid in WB_IDS[:1]:
    for domain in ("wbx-content-v2.wbstatic.net", "wbx-content-v1.wbstatic.net"):
        label = f"WB wbx {domain} {wid}"
        print(f"\n[{label}]")
        try:
            r = cr.get(f"https://{domain}/ru/{wid}/", impersonate="chrome124", timeout=20)
            print(f"  {r.status_code} | {len(r.content)}B")
            if r.status_code == 200:
                results["WB"].append({"id": wid, "method": f"wbx_{domain}", "status": "OK"})
            else:
                results["WB"].append({"id": wid, "method": f"wbx_{domain}", "status": r.status_code})
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {str(e)[:150]}")
            results["WB"].append({"id": wid, "method": f"wbx_{domain}", "status": str(e)[:80]})
        time.sleep(1)

# --- WB 4: catalog.wb.ru/v1/search (поиск по nm) ---
for wid in WB_IDS[:1]:
    label = f"WB catalog search {wid}"
    print(f"\n[{label}]")
    try:
        r = cr.get(
            "https://catalog.wb.ru/v1/search",
            params={"query": "", "resultset": "popular", "sort": "popular", "spp": 30, "nm": wid},
            impersonate="chrome124",
            timeout=15,
            headers={"Referer": "https://www.wildberries.ru/"},
        )
        print(f"  {r.status_code} | {len(r.content)}B")
        if r.status_code == 200:
            data = r.json()
            prods = data.get("data", {}).get("products", [])
            print(f"  Results: {len(prods)}")
            if prods:
                p = prods[0]
                print(f"  nm={p.get('nm')} name={p.get('name','?')[:50]}")
            results["WB"].append({"id": wid, "method": "catalog_search", "status": "OK", "count": len(prods)})
        else:
            results["WB"].append({"id": wid, "method": "catalog_search", "status": r.status_code})
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)[:150]}")
        results["WB"].append({"id": wid, "method": "catalog_search", "status": str(e)[:80]})
    time.sleep(1.5)

# --- WB 5: sp.wb.ru (new subdomain?) ---
for sub in ("sp.wb.ru", "api.wb.ru", "wbx-content-v2.wbstatic.net"):
    label = f"WB {sub}"
    print(f"\n[{label}]")
    try:
        r = cr.get(f"https://{sub}/", impersonate="chrome124", timeout=12)
        print(f"  {r.status_code} | {len(r.content)}B | CT={r.headers.get('Content-Type','?')}")
        results["WB"].append({"target": sub, "method": "root", "status": r.status_code})
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)[:150]}")
        results["WB"].append({"target": sub, "method": "root", "status": str(e)[:80]})
    time.sleep(1)

# ============================================================
# OZON
# ============================================================
print("\n" + "=" * 70)
print("OZON: verify=False + entrypoint + composer")
print("=" * 70)

ozon_sess = cr.Session(impersonate="chrome124")

# --- Ozon 1: main page (warm-up) ---
print("\n[Ozon main]")
try:
    r = ozon_sess.get("https://www.ozon.ru/", timeout=15, impersonate="chrome124")
    print(f"  {r.status_code} | {len(r.content)}B")
    if r.status_code == 200:
        cookies = dict(ozon_sess.cookies)
        print(f"  Cookies: {list(cookies.keys())[:10]}")
    results["OZON"].append({"target": "main", "status": r.status_code})
except Exception as e:
    print(f"  ERROR: {e}")
    results["OZON"].append({"target": "main", "status": str(e)[:80]})
time.sleep(3)

# --- Ozon 2: product HTML ---
for oid in OZON_IDS:
    label = f"Ozon product {oid}"
    print(f"\n[{label}]")
    try:
        r = ozon_sess.get(
            f"https://www.ozon.ru/product-{oid}/",
            timeout=15, impersonate="chrome124",
            headers={"Referer": "https://www.ozon.ru/"},
        )
        print(f"  {r.status_code} | {len(r.content)}B")
        if r.status_code == 200:
            text = r.text
            ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.DOTALL)
            if ld:
                print(f"  JSON-LD: {len(ld)} blocks")
                for i, block in enumerate(ld[:3]):
                    try:
                        obj = json.loads(block.strip())
                        if isinstance(obj, dict):
                            print(f"    [{i}] @type={obj.get('@type','?')} name={str(obj.get('name',''))[:60]}")
                            offers = obj.get("offers")
                            if offers:
                                print(f"    offers: {json.dumps(offers, ensure_ascii=False)[:200]}")
                    except:
                        pass
            og = re.findall(r'<meta[^>]*property=["\']og:(?:title|price:amount|price:currency|image)["\'][^>]*content=["\']([^"\']*)["\']', text)
            if og:
                print(f"  OG: {og[:5]}")
            results["OZON"].append({"id": oid, "method": "html", "status": "OK", "len": len(text)})
        else:
            body_preview = r.text[:200] if r.text else ""
            print(f"  Body: {body_preview}")
            results["OZON"].append({"id": oid, "method": "html", "status": r.status_code})
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)[:150]}")
        results["OZON"].append({"id": oid, "method": "html", "status": str(e)[:80]})
    time.sleep(3)

# --- Ozon 3: entrypoint-api (verify=False) ---
for oid in OZON_IDS[:1]:
    label = f"Ozon entrypoint {oid}"
    print(f"\n[{label}]")
    try:
        r = ozon_sess.get(
            "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",
            params={"url": f"/product-{oid}/"},
            timeout=15, impersonate="chrome124", verify=False,
            headers={"Referer": f"https://www.ozon.ru/product-{oid}/"},
        )
        print(f"  {r.status_code} | {len(r.content)}B")
        if r.status_code == 200:
            data = r.json()
            keys = list(data.keys())[:15]
            print(f"  Keys: {keys}")
            results["OZON"].append({"id": oid, "method": "entrypoint", "status": "OK", "keys": keys})
        else:
            results["OZON"].append({"id": oid, "method": "entrypoint", "status": r.status_code})
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)[:150]}")
        results["OZON"].append({"id": oid, "method": "entrypoint", "status": str(e)[:80]})
    time.sleep(2)

# --- Ozon 4: api.ozon.ru composer (verify=False) ---
for oid in OZON_IDS[:1]:
    label = f"Ozon composer {oid}"
    print(f"\n[{label}]")
    try:
        r = ozon_sess.get(
            "https://api.ozon.ru/composer-api.bx/page/json/v2",
            params={"url": f"/product-{oid}/"},
            timeout=15, impersonate="chrome124", verify=False,
            headers={
                "Referer": f"https://www.ozon.ru/product-{oid}/",
                "Origin": "https://www.ozon.ru",
            },
        )
        print(f"  {r.status_code} | {len(r.content)}B")
        if r.status_code == 200:
            data = r.json()
            keys = list(data.keys())[:15]
            print(f"  Keys: {keys}")
            results["OZON"].append({"id": oid, "method": "composer", "status": "OK", "keys": keys})
        else:
            results["OZON"].append({"id": oid, "method": "composer", "status": r.status_code})
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)[:150]}")
        results["OZON"].append({"id": oid, "method": "composer", "status": str(e)[:80]})
    time.sleep(2)

# --- Ozon 5: mobile ---
for oid in OZON_IDS[:1]:
    label = f"Ozon mobile {oid}"
    print(f"\n[{label}]")
    try:
        r = cr.get(
            f"https://m.ozon.ru/product-{oid}/",
            timeout=15, impersonate="chrome124",
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"},
        )
        print(f"  {r.status_code} | {len(r.content)}B")
        results["OZON"].append({"id": oid, "method": "mobile", "status": r.status_code})
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)[:150]}")
        results["OZON"].append({"id": oid, "method": "mobile", "status": str(e)[:80]})
    time.sleep(2)

# ============================================================
# AVITO
# ============================================================
print("\n" + "=" * 70)
print("AVITO: задержки + X-Requested-With + API")
print("=" * 70)

avito_sess = cr.Session(impersonate="chrome124")

# --- Avito 1: main (warm-up, долгий) ---
print("\n[Avito main]")
try:
    r = avito_sess.get("https://www.avito.ru/", timeout=20, impersonate="chrome124",
                       headers={"Accept-Language": "ru-RU,ru;q=0.9"})
    print(f"  {r.status_code} | {len(r.content)}B")
    if r.status_code == 200:
        cookies = dict(avito_sess.cookies)
        print(f"  Cookies: {list(cookies.keys())[:10]}")
    results["AVITO"].append({"target": "main", "status": r.status_code})
except Exception as e:
    print(f"  ERROR: {e}")
    results["AVITO"].append({"target": "main", "status": str(e)[:80]})
time.sleep(5)  # долгий warm-up

# --- Avito 2: product page ---
for aid in AVITO_IDS:
    label = f"Avito {aid}"
    print(f"\n[{label}]")
    try:
        r = avito_sess.get(
            f"https://www.avito.ru/vladivostok/{aid}",
            timeout=20, impersonate="chrome124",
            headers={
                "Referer": "https://www.avito.ru/vladivostok",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
        )
        print(f"  {r.status_code} | {len(r.content)}B")
        if r.status_code == 200:
            text = r.text
            ld = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.DOTALL)
            if ld:
                print(f"  JSON-LD: {len(ld)} blocks")
                for i, block in enumerate(ld[:3]):
                    try:
                        obj = json.loads(block.strip())
                        if isinstance(obj, dict):
                            print(f"    [{i}] @type={obj.get('@type','?')} name={str(obj.get('name',''))[:60]}")
                    except:
                        pass
            og = re.findall(r'<meta[^>]*property=["\']og:(?:title|price:amount|price:currency|image)["\'][^>]*content=["\']([^"\']*)["\']', text)
            if og:
                print(f"  OG: {og[:5]}")
            results["AVITO"].append({"id": aid, "method": "html", "status": "OK", "len": len(text)})
        else:
            body_preview = r.text[:200] if r.text else ""
            print(f"  Body: {body_preview}")
            results["AVITO"].append({"id": aid, "method": "html", "status": r.status_code})
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)[:150]}")
        results["AVITO"].append({"id": aid, "method": "html", "status": str(e)[:80]})
    time.sleep(5)  # долгий интервал

# --- Avito 3: API v1 ---
for aid in AVITO_IDS[:1]:
    label = f"Avito API {aid}"
    print(f"\n[{label}]")
    for api_path in (
        f"https://www.avito.ru/api/v1/items/{aid}",
        f"https://www.avito.ru/api/items/{aid}",
        f"https://www.avito.ru/vladivostok/api/items/{aid}",
    ):
        try:
            r = avito_sess.get(api_path, timeout=15, impersonate="chrome124",
                               headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
            print(f"  {api_path.split('avito.ru')[1]}: {r.status_code} | {len(r.content)}B")
            if r.status_code == 200:
                data = r.json()
                print(f"    Keys: {list(data.keys())[:10]}")
                results["AVITO"].append({"id": aid, "method": "api", "path": api_path.split("avito.ru")[1], "status": "OK"})
                break
            else:
                results["AVITO"].append({"id": aid, "method": "api", "path": api_path.split("avito.ru")[1], "status": r.status_code})
        except Exception as e:
            print(f"    ERROR: {str(e)[:100]}")
            results["AVITO"].append({"id": aid, "method": "api", "path": api_path.split("avito.ru")[1], "status": str(e)[:60]})
        time.sleep(2)

# --- Avito 4: mobile ---
for aid in AVITO_IDS[:1]:
    label = f"Avito mobile {aid}"
    print(f"\n[{label}]")
    try:
        r = avito_sess.get(
            f"https://m.avito.ru/vladivostok/{aid}",
            timeout=20, impersonate="chrome124",
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"},
        )
        print(f"  {r.status_code} | {len(r.content)}B")
        results["AVITO"].append({"id": aid, "method": "mobile", "status": r.status_code})
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)[:150]}")
        results["AVITO"].append({"id": aid, "method": "mobile", "status": str(e)[:80]})
    time.sleep(3)

# ============================================================
# SUMMARY
# ============================================================
print(f"\n{'='*70}")
print("DIAG v2 SUMMARY")
print(f"{'='*70}")
for platform, items in results.items():
    print(f"\n{platform}:")
    for item in items:
        print(f"  {item}")

out = OUT / "diag_all_v2_results.json"
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSaved: {out}")
